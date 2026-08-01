"""Azure Table Storage-backed CheckpointStorage - the multi-instance production backend.

FileCheckpointStorage (agent_framework's own default) is one host's local disk: a second
hosted-agent replica can't see the first one's checkpoints, so the design can't scale beyond
a single instance. This implements the exact same ``agent_framework.CheckpointStorage``
protocol against Azure Table Storage (or Azurite, its local emulator) instead, so
``durable/engine.py`` can select it via config with no change to ``pipeline.py`` or the
``Workflow`` itself.

Entity schema (table ``workflowcheckpoints`` by default):
    PartitionKey        = workflow_name
    RowKey              = checkpoint_id
    Data                = the checkpoint, JSON-encoded via agent_framework's own
                          ``encode_checkpoint_value`` - the same pickle+allowlist scheme
                          FileCheckpointStorage uses, just stored as a Table Storage string
                          property instead of a file.
    CheckpointTimestamp = the checkpoint's own ISO timestamp (not Table Storage's built-in
                          system ``Timestamp`` property, which isn't used for ordering here)

``load(checkpoint_id)`` doesn't receive a ``workflow_name`` - that's the ``CheckpointStorage``
protocol's own signature, not a limitation added here - so it queries by RowKey across
partitions rather than doing a partition-key point lookup. FileCheckpointStorage has the same
shape of trade-off (a flat directory keyed only by checkpoint_id); fine at demo/moderate scale,
worth revisiting (e.g. a secondary index table) if checkpoint volume grows large.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from agent_framework import WorkflowCheckpoint, WorkflowCheckpointException
from agent_framework._workflows._checkpoint_encoding import decode_checkpoint_value, encode_checkpoint_value

logger = logging.getLogger(__name__)

DEFAULT_TABLE_NAME = "workflowcheckpoints"


class TableCheckpointStorage:
    def __init__(
        self,
        *,
        connection_string: str | None = None,
        account_url: str | None = None,
        table_name: str = DEFAULT_TABLE_NAME,
        allowed_checkpoint_types: list[str] | None = None,
    ) -> None:
        from azure.data.tables.aio import TableServiceClient

        if connection_string:
            self._service = TableServiceClient.from_connection_string(connection_string)
        else:
            from azure.identity.aio import DefaultAzureCredential

            if not account_url:
                raise ValueError("TableCheckpointStorage requires either connection_string or account_url.")
            self._service = TableServiceClient(endpoint=account_url, credential=DefaultAzureCredential())

        self._table_name = table_name
        self._allowed_types = frozenset(allowed_checkpoint_types or [])
        self._table = None

    async def _get_table(self):
        if self._table is None:
            self._table = await self._service.create_table_if_not_exists(self._table_name)
        return self._table

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        table = await self._get_table()
        encoded = encode_checkpoint_value(checkpoint.to_dict())
        entity = {
            "PartitionKey": checkpoint.workflow_name,
            "RowKey": checkpoint.checkpoint_id,
            "Data": json.dumps(encoded),
            "CheckpointTimestamp": checkpoint.timestamp,
        }
        await table.upsert_entity(entity)
        logger.info("Saved checkpoint %s (workflow=%s) to Table Storage", checkpoint.checkpoint_id, checkpoint.workflow_name)
        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: str) -> WorkflowCheckpoint:
        table = await self._get_table()
        results = table.query_entities(query_filter="RowKey eq @rk", parameters={"rk": checkpoint_id})
        async for entity in results:
            return self._to_checkpoint(entity)
        raise WorkflowCheckpointException(f"No checkpoint found with ID {checkpoint_id}")

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        table = await self._get_table()
        results = table.query_entities(query_filter="PartitionKey eq @pk", parameters={"pk": workflow_name})
        checkpoints: list[WorkflowCheckpoint] = []
        async for entity in results:
            try:
                checkpoints.append(self._to_checkpoint(entity))
            except Exception as exc:  # noqa: BLE001 - one bad row shouldn't fail the whole list
                logger.warning("Failed to decode checkpoint %s: %s", entity.get("RowKey"), exc)
        return checkpoints

    async def delete(self, checkpoint_id: str) -> bool:
        table = await self._get_table()
        results = table.query_entities(query_filter="RowKey eq @rk", parameters={"rk": checkpoint_id})
        deleted = False
        async for entity in results:
            await table.delete_entity(entity["PartitionKey"], entity["RowKey"])
            deleted = True
        return deleted

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        checkpoints = await self.list_checkpoints(workflow_name=workflow_name)
        if not checkpoints:
            return None
        return max(checkpoints, key=lambda cp: datetime.fromisoformat(cp.timestamp))

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        table = await self._get_table()
        results = table.query_entities(
            query_filter="PartitionKey eq @pk", parameters={"pk": workflow_name}, select=["RowKey"]
        )
        return [entity["RowKey"] async for entity in results]

    def _to_checkpoint(self, entity: dict[str, Any]) -> WorkflowCheckpoint:
        encoded = json.loads(entity["Data"])
        decoded = decode_checkpoint_value(encoded, allowed_types=self._allowed_types)
        return WorkflowCheckpoint.from_dict(decoded)

    async def aclose(self) -> None:
        await self._service.close()
