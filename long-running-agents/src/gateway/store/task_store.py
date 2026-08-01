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
    current_message_id: str | None
    trace_id: str | None
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
        current_message_id=row["current_message_id"],
        trace_id=row["trace_id"],
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
        # T3's webhook can push either a pre-harvested `uri` (the
        # orchestrator already copied the file to the shared blob
        # container itself) or an `upstream_ref` for the gateway to
        # harvest via DurableAdapter.fetch_artifact_bytes() -- the same
        # harvest path _follow_and_relay() already runs for T2. Without
        # this, `upstream_ref` was silently dropped on the floor and a
        # T3 artifact with no pre-set `uri` could never be harvested.
        upstream_ref=payload.get("upstream_ref"),
    )


class TaskStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create_task(
        self,
        *,
        task_id: str,
        context_id: str,
        app: str,
        tier: str,
        state: TaskState,
        run_id: str | None,
        trace_id: str | None = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO gw_task (task_id, context_id, app, tier, state, run_id, trace_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                task_id,
                context_id,
                app,
                tier,
                state.value,
                run_id,
                trace_id,
            )

    async def get_task(self, task_id: str) -> TaskRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM gw_task WHERE task_id = $1", task_id)
            return _row_to_task(row) if row else None

    async def list_task_ids_for_context(self, context_id: str) -> list[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT task_id FROM gw_task WHERE context_id = $1 ORDER BY created_at DESC",
                context_id,
            )
            return [r["task_id"] for r in rows]

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

    async def get_linked_task_id(self, message_id: str) -> str | None:
        """Read side of link_inbound_message — lets a deduped retry (D7) be
        told which real task its original send produced, instead of the
        executor fabricating state for a phantom new task_id."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT task_id FROM gw_inbound_message WHERE message_id = $1", message_id
            )
            return row["task_id"] if row else None

    async def set_run_id(self, task_id: str, run_id: str | None) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE gw_task SET run_id = $2, updated_at = now() WHERE task_id = $1",
                task_id,
                run_id,
            )

    async def set_trace_id(self, task_id: str, trace_id: str | None) -> None:
        """Called on resume (GatewayAgentExecutor._continue_existing) so
        `gw_task.trace_id` reflects the trace of the most recently active
        turn -- same "overwrite, don't accumulate" reasoning as run_id and
        current_message_id. The original submit's trace_id still lives in
        whatever log lines it was written to; this column is for "what's
        the trace of the turn happening on this task right now," not a
        full history (docs/05 §6.3, docs/06 §6.3)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE gw_task SET trace_id = $2, updated_at = now() WHERE task_id = $1",
                task_id,
                trace_id,
            )

    async def set_current_message_id(self, task_id: str, message_id: str | None) -> None:
        """Points at the gw_message row (if any) holding the message
        currently in Task.status.message -- NULL when the current status
        has no associated message. Written alongside every append_messages()
        call from GatewayTaskStoreAdapter.save() so get() can split
        persisted messages back into history vs. the current status.message
        without guessing from row order alone (docs/08 item 17)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE gw_task SET current_message_id = $2, updated_at = now() WHERE task_id = $1",
                task_id,
                message_id,
            )

    async def renew_lease(self, task_id: str, lease_seconds: int) -> None:
        """Extends lease_expires_at from now, not from the previous expiry —
        a lease is "still alive, push the deadline out," not an accumulating
        grant. Called on task creation and on every event relayed through
        follow() (gateway.a2a_server.executor), so `gw_task_reaper` only
        fires once events genuinely stop arriving, not on a fixed clock
        unrelated to actual progress."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE gw_task
                SET lease_expires_at = now() + make_interval(secs => $2),
                    heartbeat_at = now()
                WHERE task_id = $1
                """,
                task_id,
                lease_seconds,
            )

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
        duplicate webhook callback is a no-op.

        Every "status" event updates `gw_task.state`, not only a `final`
        one — a non-final transition (e.g. submitted -> working) is still
        a real state change tasks/get must reflect. This was previously
        gated on `final`, so `gw_task.state` never left its initial value
        for the entire in-flight lifetime of any task, only ever updating
        once at completion; found via the interject endpoint's "is this
        task actually working" check, which could never see `working`
        because of it (docs/08). "artifact" events carry no task state and
        never touch this column.

        The terminal-state guard on the UPDATE below (`state NOT IN
        (...)`) fixes a real race, not just a defensive nicety —
        found chasing a genuinely flaky CancelTask convergence test, not
        invented speculatively. `GatewayAgentExecutor.cancel()` writes
        'canceled' directly here at the same moment the SDK is still
        draining an already-queued status event (e.g. the initial
        WORKING transition) through `GatewayTaskStoreAdapter.save()`,
        which also calls this method. Both derive `sequence` from a
        separately-fetched, stale `task_row.last_sequence + 1` read in
        Python — under concurrency they can independently compute the
        *same* next sequence number. The INSERT's `ON CONFLICT DO
        NOTHING` silently drops whichever one loses that collision, but
        its accompanying UPDATE ran unconditionally regardless — so
        whichever writer's UPDATE physically executed *last* won,
        non-deterministically. If the stale WORKING write landed after
        cancel()'s CANCELED write, state reverted to 'working' with
        nothing left to ever fix it: not a slow convergence, a genuinely
        lost cancellation. Once a task reaches a terminal state
        (`TaskState.TERMINAL_STATES` in `gateway.upstream.base` — kept
        as literal strings here since this is raw SQL, not importing the
        enum), no further status write may move it to a different
        state, regardless of arrival order — a real, always-true
        invariant this project wants, not a band-aid scoped to this one
        race."""
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
            if kind == "status":
                await conn.execute(
                    """
                    UPDATE gw_task SET state = $2, last_sequence = $3, updated_at = now()
                    WHERE task_id = $1
                      AND state NOT IN ('completed', 'failed', 'canceled', 'rejected')
                    """,
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
