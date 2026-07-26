"""gw_artifact: index and authorisation record for harvested artifacts.
The blob is the canonical bytes; this table is what makes a download
authorisable and what the `gw_artifact_unharvested` alert watches
(docs/03-postgres-schema.md, docs/07-artifacts-and-code-interpreter.md §2).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import asyncpg


@dataclass(frozen=True)
class ArtifactRow:
    artifact_id: str
    task_id: str
    name: str
    mime: str
    blob_key: str | None
    sha256: str | None
    bytes: int | None
    state: str
    upstream_ref: dict | None
    harvested_at: datetime | None
    created_at: datetime


def _row_to_artifact(row: asyncpg.Record) -> ArtifactRow:
    upstream_ref = row["upstream_ref"]
    if isinstance(upstream_ref, str):
        upstream_ref = json.loads(upstream_ref)
    return ArtifactRow(
        artifact_id=row["artifact_id"],
        task_id=row["task_id"],
        name=row["name"],
        mime=row["mime"],
        blob_key=row["blob_key"],
        sha256=row["sha256"],
        bytes=row["bytes"],
        state=row["state"],
        upstream_ref=upstream_ref,
        harvested_at=row["harvested_at"],
        created_at=row["created_at"],
    )


class ArtifactStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ensure_pending(
        self, *, artifact_id: str, task_id: str, name: str, mime: str, upstream_ref: dict | None
    ) -> ArtifactRow:
        """Idempotent: a duplicate citation — e.g. an SSE reconnect that
        replays the same poll result — re-resolves to the same row rather
        than erroring (mirrors gw_artifact_dedupe's intent, but artifact_id
        here is already the harvester's namespaced, globally-unique id —
        see gateway.artifacts.ArtifactHarvester)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO gw_artifact (artifact_id, task_id, name, mime, state, upstream_ref)
                VALUES ($1, $2, $3, $4, 'pending', $5::jsonb)
                ON CONFLICT (artifact_id) DO UPDATE SET name = EXCLUDED.name
                RETURNING *
                """,
                artifact_id,
                task_id,
                name,
                mime,
                json.dumps(upstream_ref or {}),
            )
            return _row_to_artifact(row)

    async def mark_stored(
        self, *, task_id: str, artifact_id: str, blob_key: str, sha256: str, size_bytes: int
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE gw_artifact
                SET state = 'stored', blob_key = $2, sha256 = $3, bytes = $4, harvested_at = now()
                WHERE artifact_id = $1 AND task_id = $5
                """,
                artifact_id,
                blob_key,
                sha256,
                size_bytes,
                task_id,
            )

    async def mark_failed(self, *, task_id: str, artifact_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE gw_artifact SET state = 'failed' WHERE artifact_id = $1 AND task_id = $2",
                artifact_id,
                task_id,
            )

    async def get_authorised(self, artifact_id: str, principal_subject: str) -> ArtifactRow | None:
        """Joins through gw_task -> gw_context so a download is only ever
        authorised against the caller who actually owns the conversation
        it came from (docs/07 §2 item 4) — never a bare artifact_id lookup."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT a.*
                FROM gw_artifact a
                JOIN gw_task t ON t.task_id = a.task_id
                JOIN gw_context c ON c.context_id = t.context_id
                WHERE a.artifact_id = $1 AND c.principal_subject = $2
                """,
                artifact_id,
                principal_subject,
            )
            return _row_to_artifact(row) if row else None
