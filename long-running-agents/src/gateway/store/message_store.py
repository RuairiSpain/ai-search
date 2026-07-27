"""gw_message: turn-by-turn A2A Message history. Read back by
GatewayTaskStoreAdapter.get() to populate Task.history/status.message
(docs/08-open-items-and-experiments.md item 17 -- the answer-delivery gap
this table exists to close). Keyed on message_id for idempotent re-save:
a2a-sdk's TaskManager hands TaskStore.save() the full, already-merged
task.history + status.message on every call, not a delta (verified against
the installed a2a-sdk's task_manager.py) -- same reasoning as gw_event's
(task_id, sequence) PK, applied to message_id instead since every Message
already carries its own SDK-assigned identity.
"""
from __future__ import annotations

import json

import asyncpg
from a2a.types.a2a_pb2 import Message, Role
from google.protobuf.json_format import MessageToDict, ParseDict


def _to_proto(payload: object) -> Message:
    if isinstance(payload, str):
        payload = json.loads(payload)
    return ParseDict(payload, Message(), ignore_unknown_fields=True)


class MessageStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def append_messages(self, task_id: str, messages: list[Message]) -> None:
        """Idempotent on message_id -- safe to call with a list that
        re-includes messages from a previous call, which is exactly what
        GatewayTaskStoreAdapter.save() does every time (a2a-sdk hands it
        the full history, not a delta). ON CONFLICT DO NOTHING means a
        message's `seq` is fixed at first sight, so re-saving a growing
        history never reorders anything already persisted."""
        if not messages:
            return
        async with self._pool.acquire() as conn:
            for message in messages:
                await conn.execute(
                    """
                    INSERT INTO gw_message (message_id, task_id, role, payload)
                    VALUES ($1, $2, $3, $4::jsonb)
                    ON CONFLICT (message_id) DO NOTHING
                    """,
                    message.message_id,
                    task_id,
                    Role.Name(message.role),
                    json.dumps(MessageToDict(message, preserving_proto_field_name=True)),
                )

    async def list_for_task(self, task_id: str) -> list[Message]:
        """No principal check here by design -- same posture as
        ArtifactStore.list_for_task: callers reach this only after the
        task itself has already been authorised (GatewayTaskStoreAdapter.get()
        enforces D1 before ever calling this)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM gw_message WHERE task_id = $1 ORDER BY seq", task_id
            )
        return [_to_proto(r["payload"]) for r in rows]
