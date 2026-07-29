"""Azure Table Storage-backed MetadataStore - the multi-instance production backend.

Implements the exact same async interface as ``MetadataStore`` (SQLite), so
``durable/engine.py``, ``durable/pipeline.py``, ``broker/api.py``, ``cleanup.py`` and
``stale_operations.py`` all work unchanged regardless of which one is selected via
``LDA_METADATA_BACKEND``.

Entity schema:
    operations table: PartitionKey = "operation" (fixed - the only read pattern callers use
        is a point lookup by operation_id alone, so a single partition keeps that a true
        O(1) lookup), RowKey = operation_id.
    artifacts table: PartitionKey = "artifact" (fixed, same reasoning), RowKey = artifact_id.
    steering messages table: PartitionKey = operation_id (this one *is* the natural grouping,
        since drain_steering_messages always reads/clears one operation's queue at a time),
        RowKey = a zero-padded millisecond timestamp + short random suffix, so entities within
        a partition - which Table Storage returns in RowKey order - come back in arrival order.

Table Storage entities can't store a literal null; absent optional fields (artifact_id,
pending_request_id, error) are stored as "" and translated back to None on read, matching
sqlite3.Row's semantics for the same columns.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import ArtifactRecord

OPERATION_PARTITION = "operation"
ARTIFACT_PARTITION = "artifact"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _empty_if_none(v: Optional[str]) -> str:
    return v if v is not None else ""


def _none_if_empty(v: Any) -> Optional[str]:
    return v if v else None


def _sortable_row_key() -> str:
    """Zero-padded so entities within a partition sort chronologically by RowKey."""
    return f"{int(time.time() * 1000):020d}-{uuid.uuid4().hex[:8]}"


class TableMetadataStore:
    def __init__(
        self,
        *,
        connection_string: str | None = None,
        account_url: str | None = None,
        operations_table: str = "operations",
        artifacts_table: str = "artifacts",
        steering_table: str = "steeringmessages",
    ) -> None:
        from azure.data.tables.aio import TableServiceClient

        if connection_string:
            self._service = TableServiceClient.from_connection_string(connection_string)
        else:
            from azure.identity.aio import DefaultAzureCredential

            if not account_url:
                raise ValueError("TableMetadataStore requires either connection_string or account_url.")
            self._service = TableServiceClient(endpoint=account_url, credential=DefaultAzureCredential())

        self._operations_table_name = operations_table
        self._artifacts_table_name = artifacts_table
        self._steering_table_name = steering_table
        self._operations_table = None
        self._artifacts_table = None
        self._steering_table = None

    async def _get_operations_table(self):
        if self._operations_table is None:
            self._operations_table = await self._service.create_table_if_not_exists(self._operations_table_name)
        return self._operations_table

    async def _get_artifacts_table(self):
        if self._artifacts_table is None:
            self._artifacts_table = await self._service.create_table_if_not_exists(self._artifacts_table_name)
        return self._artifacts_table

    async def _get_steering_table(self):
        if self._steering_table is None:
            self._steering_table = await self._service.create_table_if_not_exists(self._steering_table_name)
        return self._steering_table

    # ---- operations -----------------------------------------------------

    @staticmethod
    def _operation_entity_to_dict(entity) -> dict:
        return {
            "operation_id": entity["RowKey"],
            "workflow_name": entity["workflow_name"],
            "tenant_id": entity["tenant_id"],
            "user_object_id": entity["user_object_id"],
            "status": entity["status"],
            "artifact_id": _none_if_empty(entity.get("artifact_id")),
            "pending_request_id": _none_if_empty(entity.get("pending_request_id")),
            "error": _none_if_empty(entity.get("error")),
            "created_at": entity["created_at"],
            "updated_at": entity["updated_at"],
        }

    async def get_operation(self, operation_id: str) -> Optional[dict]:
        from azure.core.exceptions import ResourceNotFoundError

        table = await self._get_operations_table()
        try:
            entity = await table.get_entity(OPERATION_PARTITION, operation_id)
        except ResourceNotFoundError:
            return None
        return self._operation_entity_to_dict(entity)

    async def start_operation(
        self, *, operation_id: str, workflow_name: str, tenant_id: str, user_object_id: str
    ) -> dict:
        from azure.core.exceptions import ResourceExistsError

        existing = await self.get_operation(operation_id)
        if existing is not None:
            return existing

        now = _to_iso(_now())
        table = await self._get_operations_table()
        entity = {
            "PartitionKey": OPERATION_PARTITION,
            "RowKey": operation_id,
            "workflow_name": workflow_name,
            "tenant_id": tenant_id,
            "user_object_id": user_object_id,
            "status": "in_progress",
            "artifact_id": "",
            "pending_request_id": "",
            "error": "",
            "created_at": now,
            "updated_at": now,
        }
        try:
            await table.create_entity(entity)
        except ResourceExistsError:
            pass  # a concurrent caller created it first - fall through and read it back
        return await self.get_operation(operation_id)  # type: ignore[return-value]

    async def _update_operation(self, operation_id: str, **fields: Any) -> None:
        table = await self._get_operations_table()
        entity = {"PartitionKey": OPERATION_PARTITION, "RowKey": operation_id, **fields}
        await table.upsert_entity(entity, mode="merge")

    async def complete_operation(self, operation_id: str, *, artifact_id: str) -> None:
        await self._update_operation(
            operation_id,
            status="completed",
            artifact_id=artifact_id,
            pending_request_id="",
            updated_at=_to_iso(_now()),
        )

    async def fail_operation(self, operation_id: str, *, error: str) -> None:
        await self._update_operation(
            operation_id, status="failed", error=error, pending_request_id="", updated_at=_to_iso(_now())
        )

    async def stop_operation(self, operation_id: str) -> None:
        await self._update_operation(
            operation_id, status="stopped", pending_request_id="", updated_at=_to_iso(_now())
        )

    async def set_waiting_on_hitl(self, operation_id: str, *, request_id: str) -> None:
        await self._update_operation(
            operation_id, status="waiting_hitl", pending_request_id=request_id, updated_at=_to_iso(_now())
        )

    async def mark_in_progress(self, operation_id: str) -> None:
        await self._update_operation(
            operation_id, status="in_progress", pending_request_id="", updated_at=_to_iso(_now())
        )

    async def list_stale_operations(self, *, older_than: datetime) -> list[dict]:
        table = await self._get_operations_table()
        cutoff = _to_iso(older_than)
        results = table.query_entities(
            query_filter=(
                "PartitionKey eq @pk and (status eq 'in_progress' or status eq 'waiting_hitl') "
                "and updated_at le @cutoff"
            ),
            parameters={"pk": OPERATION_PARTITION, "cutoff": cutoff},
        )
        return [self._operation_entity_to_dict(entity) async for entity in results]

    # ---- steering messages -------------------------------------------------

    async def queue_steering_message(
        self, *, operation_id: str, tenant_id: str, user_object_id: str, text: str
    ) -> None:
        table = await self._get_steering_table()
        entity = {
            "PartitionKey": operation_id,
            "RowKey": _sortable_row_key(),
            "tenant_id": tenant_id,
            "user_object_id": user_object_id,
            "text": text,
            "created_at": _to_iso(_now()),
        }
        await table.create_entity(entity)

    async def drain_steering_messages(self, operation_id: str) -> list[str]:
        table = await self._get_steering_table()
        results = table.query_entities(query_filter="PartitionKey eq @pk", parameters={"pk": operation_id})
        entities = [entity async for entity in results]  # RowKey order == arrival order
        for entity in entities:
            await table.delete_entity(entity["PartitionKey"], entity["RowKey"])
        return [entity["text"] for entity in entities]

    # ---- artifacts --------------------------------------------------------

    @staticmethod
    def _artifact_entity_to_record(entity) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=entity["RowKey"],
            operation_id=entity["operation_id"],
            tenant_id=entity["tenant_id"],
            user_object_id=entity["user_object_id"],
            blob_container=entity["blob_container"],
            blob_name=entity["blob_name"],
            display_name=entity["display_name"],
            content_type=entity["content_type"],
            size_bytes=entity["size_bytes"],
            created_at=_from_iso(entity["created_at"]),
            expires_at=_from_iso(entity["expires_at"]),
            status=entity["status"],
        )

    async def save_artifact(self, record: ArtifactRecord) -> None:
        table = await self._get_artifacts_table()
        entity = {
            "PartitionKey": ARTIFACT_PARTITION,
            "RowKey": record.artifact_id,
            "operation_id": record.operation_id,
            "tenant_id": record.tenant_id,
            "user_object_id": record.user_object_id,
            "blob_container": record.blob_container,
            "blob_name": record.blob_name,
            "display_name": record.display_name,
            "content_type": record.content_type,
            "size_bytes": record.size_bytes,
            "created_at": _to_iso(record.created_at),
            "expires_at": _to_iso(record.expires_at),
            "status": record.status,
        }
        await table.upsert_entity(entity, mode="replace")

    async def get_artifact(self, artifact_id: str) -> Optional[ArtifactRecord]:
        from azure.core.exceptions import ResourceNotFoundError

        table = await self._get_artifacts_table()
        try:
            entity = await table.get_entity(ARTIFACT_PARTITION, artifact_id)
        except ResourceNotFoundError:
            return None
        return self._artifact_entity_to_record(entity)

    async def list_expired(self, *, now: Optional[datetime] = None) -> list[ArtifactRecord]:
        table = await self._get_artifacts_table()
        cutoff = _to_iso(now or _now())
        results = table.query_entities(
            query_filter="PartitionKey eq @pk and status eq 'active' and expires_at le @cutoff",
            parameters={"pk": ARTIFACT_PARTITION, "cutoff": cutoff},
        )
        return [self._artifact_entity_to_record(entity) async for entity in results]

    async def mark_deleted(self, artifact_id: str) -> None:
        table = await self._get_artifacts_table()
        await table.upsert_entity(
            {"PartitionKey": ARTIFACT_PARTITION, "RowKey": artifact_id, "status": "deleted"}, mode="merge"
        )

    async def aclose(self) -> None:
        await self._service.close()
