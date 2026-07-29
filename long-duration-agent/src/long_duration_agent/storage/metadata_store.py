"""Metadata for operations and artifacts.

Deliberately minimal - this is bookkeeping, not an artifact catalogue (the
user explicitly does not want a "my artifacts" list/browse feature). It
stores just enough to: resume an in-flight operation idempotently, know
which blob belongs to which artifact_id, enforce per-user ownership before
the broker will stream a download, and sweep expired (TTL) artifacts.

No SAS token, download URL, or other credential is ever persisted here -
see broker/tokens.py, which mints a fresh short-lived token per download
request instead.

SQLite is intentionally swappable: everything here goes through a small
repository interface so a production deployment can point this at Azure
Table Storage / Cosmos DB without touching callers. See docs/architecture.md.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from ..models import ArtifactRecord


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


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

    def get_operation(self, operation_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()

    def start_operation(
        self, *, operation_id: str, workflow_name: str, tenant_id: str, user_object_id: str
    ) -> sqlite3.Row:
        existing = self.get_operation(operation_id)
        if existing is not None:
            return existing
        now = _to_iso(_now())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO operations
                   (operation_id, workflow_name, tenant_id, user_object_id, status, artifact_id,
                    pending_request_id, error, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'in_progress', NULL, NULL, NULL, ?, ?)""",
                (operation_id, workflow_name, tenant_id, user_object_id, now, now),
            )
        return self.get_operation(operation_id)  # type: ignore[return-value]

    def complete_operation(self, operation_id: str, *, artifact_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE operations
                   SET status = 'completed', artifact_id = ?, pending_request_id = NULL, updated_at = ?
                   WHERE operation_id = ?""",
                (artifact_id, _to_iso(_now()), operation_id),
            )

    def fail_operation(self, operation_id: str, *, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE operations
                   SET status = 'failed', error = ?, pending_request_id = NULL, updated_at = ?
                   WHERE operation_id = ?""",
                (error, _to_iso(_now()), operation_id),
            )

    def stop_operation(self, operation_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE operations
                   SET status = 'stopped', pending_request_id = NULL, updated_at = ?
                   WHERE operation_id = ?""",
                (_to_iso(_now()), operation_id),
            )

    def set_waiting_on_hitl(self, operation_id: str, *, request_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE operations
                   SET status = 'waiting_hitl', pending_request_id = ?, updated_at = ?
                   WHERE operation_id = ?""",
                (request_id, _to_iso(_now()), operation_id),
            )

    def mark_in_progress(self, operation_id: str) -> None:
        """Clears a resolved HITL pause so the operation reads as actively running again."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE operations
                   SET status = 'in_progress', pending_request_id = NULL, updated_at = ?
                   WHERE operation_id = ?""",
                (_to_iso(_now()), operation_id),
            )

    # ---- steering messages -------------------------------------------------

    def queue_steering_message(self, *, operation_id: str, tenant_id: str, user_object_id: str, text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO steering_messages (operation_id, tenant_id, user_object_id, text, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (operation_id, tenant_id, user_object_id, text, _to_iso(_now())),
            )

    def drain_steering_messages(self, operation_id: str) -> list[str]:
        """Returns all queued steering texts for this operation, in arrival order, and clears them."""
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

    # ---- artifacts --------------------------------------------------------

    def save_artifact(self, record: ArtifactRecord) -> None:
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

    def get_artifact(self, artifact_id: str) -> Optional[ArtifactRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
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


    def list_expired(self, *, now: Optional[datetime] = None) -> list[ArtifactRecord]:
        cutoff = _to_iso(now or _now())
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT artifact_id FROM artifacts WHERE status = 'active' AND expires_at <= ?", (cutoff,)
            ).fetchall()
        records = (self.get_artifact(row["artifact_id"]) for row in rows)
        return [record for record in records if record is not None]

    def mark_deleted(self, artifact_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE artifacts SET status = 'deleted' WHERE artifact_id = ?", (artifact_id,))


_STORE: MetadataStore | None = None


def get_metadata_store() -> MetadataStore:
    global _STORE
    if _STORE is None:
        from ..config import get_settings

        _STORE = MetadataStore(get_settings().state_db_path)
    return _STORE


def reset_metadata_store_cache() -> None:
    global _STORE
    _STORE = None
