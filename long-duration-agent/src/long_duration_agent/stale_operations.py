"""Sweeps operations that never got resumed (default: 6 hours).

An operation stuck ``in_progress`` (the worker crashed between checkpoints and nothing ever
retried it) or ``waiting_hitl`` (the user was asked to confirm a steering message and never
answered - closed the tab, walked away) sits forever otherwise: its checkpoint stays on disk/
in Table Storage, and - for waiting_hitl - the user could in principle come back and resume it
arbitrarily far in the future. This sweep force-stops anything past the configured age, the
same cleanup a user-initiated "stop" HITL decision performs: delete the hosted agent's local
scratch file and mark the operation stopped. There's deliberately nothing to delete in Blob
Storage here - complete_operation() (and therefore an artifact_id) is only ever set together
with status='completed', which this sweep's query excludes by construction, so a swept
operation never has an uploaded artifact to clean up.

Run periodically alongside cleanup.py (cron, an Azure Function timer trigger, a Kubernetes
CronJob, ...):
    python -m long_duration_agent.stale_operations
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .storage.metadata_store import get_metadata_store
from .workspace import delete_workspace_file

logger = logging.getLogger(__name__)


async def sweep_stale_operations() -> int:
    store = get_metadata_store()
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.lda_operation_stale_hours)

    stale = await store.list_stale_operations(older_than=cutoff)
    for operation in stale:
        operation_id = operation["operation_id"]
        delete_workspace_file(operation_id)
        await store.stop_operation(operation_id)
        logger.info(
            "Swept stale operation %s (status=%s, owner %s/%s, last updated %s)",
            operation_id,
            operation["status"],
            operation["tenant_id"],
            operation["user_object_id"],
            operation["updated_at"],
        )
    return len(stale)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = asyncio.run(sweep_stale_operations())
    print(f"Swept {count} stale operation(s).")
