"""Metadata for operations and artifacts.

Deliberately minimal - this is bookkeeping, not an artifact catalogue (the
user explicitly does not want a "my artifacts" list/browse feature). It
stores just enough to: resume an in-flight operation idempotently, know
which blob belongs to which artifact_id, sweep expired (TTL) artifacts, and
sweep stale operations that never got resumed.

No SAS token, download URL, or other credential is ever persisted here - see
storage/blob_store.py's ``generate_download_url``, which mints a fresh
short-lived SAS URL per download request instead, straight from Blob
Storage, with no broker/proxy in between.

All methods are async - not because SQLite needs it (it doesn't have a
non-blocking API, so the SQLite implementation below runs its queries via
``asyncio.to_thread``), but so ``TableMetadataStore`` (table_metadata_store.py),
whose real network calls to Azure Table Storage would otherwise block the
event loop, can implement the exact same interface. See docs/architecture.md.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping, Optional, Protocol

from ..models import ArtifactRecord, OrchestrationStage, StreamEvent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


class MetadataStoreProtocol(Protocol):
    """The interface both MetadataStore (SQLite) and TableMetadataStore implement."""

    async def get_operation(self, operation_id: str) -> Optional[Mapping]: ...

    async def start_operation(
        self, *, operation_id: str, workflow_name: str, tenant_id: str, user_object_id: str
    ) -> Mapping: ...

    async def complete_operation(self, operation_id: str, *, artifact_id: str) -> None: ...

    async def fail_operation(self, operation_id: str, *, error: str) -> None: ...

    async def stop_operation(self, operation_id: str) -> None: ...

    async def set_waiting_on_hitl(self, operation_id: str, *, request_id: str) -> None: ...

    async def mark_in_progress(self, operation_id: str) -> None: ...

    async def list_stale_operations(self, *, older_than: datetime) -> list[Mapping]: ...

    async def append_event(self, operation_id: str, event: StreamEvent) -> None: ...

    async def list_events(self, operation_id: str) -> list[StreamEvent]: ...

    async def queue_steering_message(
        self, *, operation_id: str, tenant_id: str, user_object_id: str, text: str
    ) -> None: ...

    async def drain_steering_messages(self, operation_id: str) -> list[str]: ...

    async def save_artifact(self, record: ArtifactRecord) -> None: ...

    async def get_artifact(self, artifact_id: str) -> Optional[ArtifactRecord]: ...

    async def list_expired(self, *, now: Optional[datetime] = None) -> list[ArtifactRecord]: ...

    async def mark_deleted(self, artifact_id: str) -> None: ...


SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    operation_id       TEXT PRIMARY KEY,
    workflow_name      TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    user_object_id     TEXT NOT NULL,
    status             TEXT NOT NULL,
    artifact_id        TEXT,
    pending_request_id TEXT,
    error              TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steering_messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id   TEXT NOT NULL,
    tenant_id      TEXT NOT NULL,
    user_object_id TEXT NOT NULL,
    text           TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_steering_operation ON steering_messages (operation_id);

CREATE TABLE IF NOT EXISTS operation_events (
    operation_id TEXT NOT NULL,
    sequence     INTEGER NOT NULL,
    event        TEXT NOT NULL,
    stage        TEXT NOT NULL,
    data         TEXT NOT NULL,
    emitted_at   TEXT NOT NULL,
    PRIMARY KEY (operation_id, sequence)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id     TEXT PRIMARY KEY,
    operation_id    TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    user_object_id  TEXT NOT NULL,
    blob_container  TEXT NOT NULL,
    blob_name       TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    status          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_owner ON artifacts (tenant_id, user_object_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_expires ON artifacts (expires_at);
"""


class MetadataStore:
    """SQLite-backed implementation - single host, zero external dependencies."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- operations -----------------------------------------------------

    async def get_operation(self, operation_id: str) -> Optional[sqlite3.Row]:
        def _run() -> Optional[sqlite3.Row]:
            with self._connect() as conn:
                return conn.execute(
                    "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
                ).fetchone()

        return await asyncio.to_thread(_run)

    async def start_operation(
        self, *, operation_id: str, workflow_name: str, tenant_id: str, user_object_id: str
    ) -> sqlite3.Row:
        existing = await self.get_operation(operation_id)
        if existing is not None:
            return existing
        now = _to_iso(_now())

        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO operations
                       (operation_id, workflow_name, tenant_id, user_object_id, status, artifact_id,
                        pending_request_id, error, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'in_progress', NULL, NULL, NULL, ?, ?)""",
                    (operation_id, workflow_name, tenant_id, user_object_id, now, now),
                )

        await asyncio.to_thread(_run)
        return await self.get_operation(operation_id)  # type: ignore[return-value]

    async def complete_operation(self, operation_id: str, *, artifact_id: str) -> None:
        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE operations
                       SET status = 'completed', artifact_id = ?, pending_request_id = NULL, updated_at = ?
                       WHERE operation_id = ?""",
                    (artifact_id, _to_iso(_now()), operation_id),
                )

        await asyncio.to_thread(_run)

    async def fail_operation(self, operation_id: str, *, error: str) -> None:
        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE operations
                       SET status = 'failed', error = ?, pending_request_id = NULL, updated_at = ?
                       WHERE operation_id = ?""",
                    (error, _to_iso(_now()), operation_id),
                )

        await asyncio.to_thread(_run)

    async def stop_operation(self, operation_id: str) -> None:
        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE operations
                       SET status = 'stopped', pending_request_id = NULL, updated_at = ?
                       WHERE operation_id = ?""",
                    (_to_iso(_now()), operation_id),
                )

        await asyncio.to_thread(_run)

    async def set_waiting_on_hitl(self, operation_id: str, *, request_id: str) -> None:
        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE operations
                       SET status = 'waiting_hitl', pending_request_id = ?, updated_at = ?
                       WHERE operation_id = ?""",
                    (request_id, _to_iso(_now()), operation_id),
                )

        await asyncio.to_thread(_run)

    async def mark_in_progress(self, operation_id: str) -> None:
        """Clears a resolved HITL pause so the operation reads as actively running again."""

        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE operations
                       SET status = 'in_progress', pending_request_id = NULL, updated_at = ?
                       WHERE operation_id = ?""",
                    (_to_iso(_now()), operation_id),
                )

        await asyncio.to_thread(_run)

    async def list_stale_operations(self, *, older_than: datetime) -> list[sqlite3.Row]:
        """Operations still in_progress/waiting_hitl whose updated_at predates the cutoff -
        candidates for stale_operations.py's sweep."""
        cutoff = _to_iso(older_than)

        def _run() -> list[sqlite3.Row]:
            with self._connect() as conn:
                return conn.execute(
                    "SELECT * FROM operations WHERE status IN ('in_progress', 'waiting_hitl') AND updated_at <= ?",
                    (cutoff,),
                ).fetchall()

        return await asyncio.to_thread(_run)

    # ---- durable event log (for reconnects) --------------------------------

    async def append_event(self, operation_id: str, event: StreamEvent) -> None:
        """Persists one emitted StreamEvent. Idempotent by (operation_id, sequence) - a
        duplicate append (there shouldn't be one in normal operation) is silently ignored
        rather than raising, matching this codebase's general idempotent-replay posture."""

        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO operation_events
                       (operation_id, sequence, event, stage, data, emitted_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        operation_id,
                        event.sequence,
                        event.event,
                        event.stage.value,
                        json.dumps(event.data),
                        _to_iso(event.emitted_at),
                    ),
                )

        await asyncio.to_thread(_run)

    async def list_events(self, operation_id: str) -> list[StreamEvent]:
        """Returns every event persisted for this operation, in emission order - what a
        reconnecting client replays before live events resume."""

        def _run() -> list[sqlite3.Row]:
            with self._connect() as conn:
                return conn.execute(
                    "SELECT * FROM operation_events WHERE operation_id = ? ORDER BY sequence ASC",
                    (operation_id,),
                ).fetchall()

        rows = await asyncio.to_thread(_run)
        return [
            StreamEvent(
                event=row["event"],
                stage=OrchestrationStage(row["stage"]),
                data=json.loads(row["data"]),
                sequence=row["sequence"],
                emitted_at=_from_iso(row["emitted_at"]),
            )
            for row in rows
        ]

    # ---- steering messages -------------------------------------------------

    async def queue_steering_message(
        self, *, operation_id: str, tenant_id: str, user_object_id: str, text: str
    ) -> None:
        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO steering_messages (operation_id, tenant_id, user_object_id, text, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (operation_id, tenant_id, user_object_id, text, _to_iso(_now())),
                )

        await asyncio.to_thread(_run)

    async def drain_steering_messages(self, operation_id: str) -> list[str]:
        """Returns all queued steering texts for this operation, in arrival order, and clears them."""

        def _run() -> list[str]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, text FROM steering_messages WHERE operation_id = ? ORDER BY id ASC",
                    (operation_id,),
                ).fetchall()
                if rows:
                    conn.execute(
                        "DELETE FROM steering_messages WHERE operation_id = ?",
                        (operation_id,),
                    )
            return [row["text"] for row in rows]

        return await asyncio.to_thread(_run)

    # ---- artifacts --------------------------------------------------------

    async def save_artifact(self, record: ArtifactRecord) -> None:
        def _run() -> None:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO artifacts
                       (artifact_id, operation_id, tenant_id, user_object_id, blob_container, blob_name,
                        display_name, content_type, size_bytes, created_at, expires_at, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(artifact_id) DO UPDATE SET
                         blob_container=excluded.blob_container, blob_name=excluded.blob_name,
                         display_name=excluded.display_name, content_type=excluded.content_type,
                         size_bytes=excluded.size_bytes, expires_at=excluded.expires_at, status=excluded.status
                    """,
                    (
                        record.artifact_id,
                        record.operation_id,
                        record.tenant_id,
                        record.user_object_id,
                        record.blob_container,
                        record.blob_name,
                        record.display_name,
                        record.content_type,
                        record.size_bytes,
                        _to_iso(record.created_at),
                        _to_iso(record.expires_at),
                        record.status,
                    ),
                )

        await asyncio.to_thread(_run)

    async def get_artifact(self, artifact_id: str) -> Optional[ArtifactRecord]:
        def _run() -> Optional[sqlite3.Row]:
            with self._connect() as conn:
                return conn.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()

        row = await asyncio.to_thread(_run)
        if row is None:
            return None
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            operation_id=row["operation_id"],
            tenant_id=row["tenant_id"],
            user_object_id=row["user_object_id"],
            blob_container=row["blob_container"],
            blob_name=row["blob_name"],
            display_name=row["display_name"],
            content_type=row["content_type"],
            size_bytes=row["size_bytes"],
            created_at=_from_iso(row["created_at"]),
            expires_at=_from_iso(row["expires_at"]),
            status=row["status"],
        )

    async def list_expired(self, *, now: Optional[datetime] = None) -> list[ArtifactRecord]:
        cutoff = _to_iso(now or _now())

        def _run() -> list[sqlite3.Row]:
            with self._connect() as conn:
                return conn.execute(
                    "SELECT artifact_id FROM artifacts WHERE status = 'active' AND expires_at <= ?", (cutoff,)
                ).fetchall()

        rows = await asyncio.to_thread(_run)
        records = [await self.get_artifact(row["artifact_id"]) for row in rows]
        return [record for record in records if record is not None]

    async def mark_deleted(self, artifact_id: str) -> None:
        def _run() -> None:
            with self._connect() as conn:
                conn.execute("UPDATE artifacts SET status = 'deleted' WHERE artifact_id = ?", (artifact_id,))

        await asyncio.to_thread(_run)


_STORE: MetadataStoreProtocol | None = None


def get_metadata_store() -> MetadataStoreProtocol:
    global _STORE
    if _STORE is None:
        from ..config import get_settings

        settings = get_settings()
        backend = settings.lda_metadata_backend
        if backend in ("azurite", "azure"):
            from .table_metadata_store import TableMetadataStore

            _STORE = TableMetadataStore(
                connection_string=settings.azurite_connection_string if backend == "azurite" else None,
                account_url=settings.azure_table_account_url if backend == "azure" else None,
                operations_table=settings.lda_operations_table_name,
                artifacts_table=settings.lda_artifacts_table_name,
                steering_table=settings.lda_steering_table_name,
                events_table=settings.lda_events_table_name,
            )
        else:
            _STORE = MetadataStore(settings.state_db_path)
    return _STORE


def reset_metadata_store_cache() -> None:
    global _STORE
    _STORE = None
