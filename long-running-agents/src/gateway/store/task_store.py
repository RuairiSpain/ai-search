"""gw_task / gw_event / gw_inbound_message.

Implements gateway.upstream.durable.EventSource so the T3 adapter's
follow() can relay webhook-pushed events without importing SQL directly
(docs/00 design premise #3). Uses LISTEN/NOTIFY on gw_event so a callback
landing on one replica reaches an SSE stream held by another
(docs/03-postgres-schema.md "Cross-replica event fan-in").
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from gateway.upstream.base import ArtifactEvent, StatusEvent, TaskState


@dataclass(frozen=True)
class TaskRow:
    task_id: str
    context_id: str
    app: str
    tier: str
    state: str
    run_id: str | None
    last_sequence: int
    created_at: datetime
    updated_at: datetime


def _row_to_task(row: asyncpg.Record) -> TaskRow:
    return TaskRow(
        task_id=row["task_id"],
        context_id=row["context_id"],
        app=row["app"],
        tier=row["tier"],
        state=row["state"],
        run_id=row["run_id"],
        last_sequence=row["last_sequence"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event_from_row(row: asyncpg.Record) -> StatusEvent | ArtifactEvent:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    if row["kind"] == "status":
        return StatusEvent(
            task_id=row["task_id"],
            state=TaskState(payload["state"]),
            sequence=row["sequence"],
            detail=payload.get("detail"),
            final=payload.get("final", False),
        )
    return ArtifactEvent(
        task_id=row["task_id"],
        artifact_id=payload["artifact_id"],
        name=payload["name"],
        mime=payload["mime"],
        sequence=row["sequence"],
        uri=payload.get("uri"),
    )


class TaskStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create_task(
        self, *, task_id: str, context_id: str, app: str, tier: str, state: TaskState, run_id: str | None
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO gw_task (task_id, context_id, app, tier, state, run_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                task_id,
                context_id,
                app,
                tier,
                state.value,
                run_id,
            )

    async def get_task(self, task_id: str) -> TaskRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM gw_task WHERE task_id = $1", task_id)
            return _row_to_task(row) if row else None

    async def dedupe_inbound(self, message_id: str) -> bool:
        """True if this messageId is new (proceed); False if it's a
        retry we've already handled (docs/02-decisions.md D7 "Submit
        idempotency" — dedupe on messageId *before* the upstream call,
        which is necessarily before a task_id exists — see
        link_inbound_message)."""
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    "INSERT INTO gw_inbound_message (message_id) VALUES ($1)", message_id
                )
                return True
            except asyncpg.UniqueViolationError:
                return False

    async def link_inbound_message(self, message_id: str, task_id: str) -> None:
        """Second step of dedupe_inbound: once the task actually exists,
        link the message that caused it — for audit ("what task did this
        message produce"), not for the idempotency check itself, which
        already happened in dedupe_inbound."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE gw_inbound_message SET task_id = $2 WHERE message_id = $1",
                message_id,
                task_id,
            )

    async def append_event(
        self, task_id: str, sequence: int, kind: str, payload: dict
    ) -> None:
        """Idempotent: (task_id, sequence) is the primary key, so a
        duplicate webhook callback is a no-op."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO gw_event (task_id, sequence, kind, payload)
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (task_id, sequence) DO NOTHING
                """,
                task_id,
                sequence,
                kind,
                json.dumps(payload),
            )
            if kind == "status" and payload.get("final"):
                await conn.execute(
                    "UPDATE gw_task SET state = $2, last_sequence = $3, updated_at = now() "
                    "WHERE task_id = $1",
                    task_id,
                    payload["state"],
                    sequence,
                )
            else:
                await conn.execute(
                    "UPDATE gw_task SET last_sequence = $2, updated_at = now() WHERE task_id = $1",
                    task_id,
                    sequence,
                )

    # -- EventSource protocol (consumed by gateway.upstream.durable) --

    async def events_after(self, task_id: str, from_sequence: int) -> list[StatusEvent | ArtifactEvent]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM gw_event WHERE task_id = $1 AND sequence > $2 ORDER BY sequence",
                task_id,
                from_sequence,
            )
            return [_event_from_row(r) for r in rows]

    async def wait_for_new_event(self, task_id: str, timeout_s: float) -> bool:
        got_notification = asyncio.Event()

        def _on_notify(_conn, _pid, _channel, payload: str) -> None:
            if payload == task_id:
                got_notification.set()

        conn = await self._pool.acquire()
        try:
            await conn.add_listener("gw_event", _on_notify)
            try:
                await asyncio.wait_for(got_notification.wait(), timeout=timeout_s)
                return True
            except TimeoutError:
                return False
            finally:
                await conn.remove_listener("gw_event", _on_notify)
        finally:
            await self._pool.release(conn)

    async def reap_wedged_tasks(self, *, lease_grace_s: int) -> list[str]:
        """Fail tasks whose lease has lapsed. Deliberately excludes
        `input-required` — a multi-day HITL approval is not wedged
        (docs/06-tier3-durable-agents.md §5.3)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE gw_task
                SET state = 'failed', updated_at = now()
                WHERE state IN ('submitted', 'working')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < now() - make_interval(secs => $1)
                RETURNING task_id
                """,
                lease_grace_s,
            )
            return [r["task_id"] for r in rows]
