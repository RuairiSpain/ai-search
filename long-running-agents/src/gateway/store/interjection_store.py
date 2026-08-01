"""gw_interjection: user interjections into a running task (D7). Deliberately
separate from gw_event: events are things the upstream told us, interjections
are things the user asked us to tell the upstream — different direction,
different lifecycle (docs/03-postgres-schema.md).
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import datetime

import asyncpg


@dataclass(frozen=True)
class InterjectionRow:
    task_id: str
    sequence: int
    principal_subject: str
    text: str
    state: str
    created_at: datetime
    delivered_at: datetime | None


def _row_to_interjection(row: asyncpg.Record) -> InterjectionRow:
    return InterjectionRow(
        task_id=row["task_id"],
        sequence=row["sequence"],
        principal_subject=row["principal_subject"],
        text=row["text"],
        state=row["state"],
        created_at=row["created_at"],
        delivered_at=row["delivered_at"],
    )


class InterjectionStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def record(
        self, *, task_id: str, principal_subject: str, text: str, delivered: bool
    ) -> InterjectionRow:
        """`delivered` reflects whether `adapter.steer()` actually handed
        the text to the upstream before this is called, not whether it has
        taken effect yet (D7's `SteerResult.outcome` already distinguishes
        `queued` from `accepted` for that; this row's `state` only tracks
        gateway-side delivery). Sequence assignment is advisory-locked per
        task_id — same pattern as `context_store`'s session-creation race
        fix — since two interjections landing in the same instant must not
        collide on sequence."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)", zlib.crc32(task_id.encode())
            )
            next_seq = await conn.fetchval(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM gw_interjection WHERE task_id = $1",
                task_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO gw_interjection (task_id, sequence, principal_subject, text, state, delivered_at)
                VALUES ($1, $2, $3, $4, $5, CASE WHEN $6 THEN now() ELSE NULL END)
                RETURNING *
                """,
                task_id,
                next_seq,
                principal_subject,
                text,
                "delivered" if delivered else "pending",
                delivered,
            )
            return _row_to_interjection(row)

    async def list_for_task(self, task_id: str) -> list[InterjectionRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM gw_interjection WHERE task_id = $1 ORDER BY sequence", task_id
            )
            return [_row_to_interjection(r) for r in rows]
