"""TTL sweeper for expired artifacts (default: 1 day).

In production the storage account's own lifecycle management policy
(infra/storage-public.bicep) deletes the blob automatically - this sweeper
exists to keep the metadata store in sync (mark the row deleted so
run_translation_operation's idempotent replay reports "expired" instead of
minting a fresh SAS link for a blob that's already gone) and to clean up in
the local-disk demo backend, which has no lifecycle policy of its own.

Run periodically (cron, an Azure Function timer trigger, a Kubernetes
CronJob, ...):
    python -m long_duration_agent.cleanup
"""

from __future__ import annotations

import asyncio
import logging

from .storage.blob_store import get_blob_store
from .storage.metadata_store import get_metadata_store

logger = logging.getLogger(__name__)


async def sweep_expired_artifacts() -> int:
    store = get_metadata_store()
    blob_store = get_blob_store()
    expired = await store.list_expired()
    for record in expired:
        try:
            await blob_store.delete(record.blob_name)
        except FileNotFoundError:
            pass  # already removed by the storage account's own lifecycle policy
        await store.mark_deleted(record.artifact_id)
        logger.info("Swept expired artifact %s (owner %s/%s)", record.artifact_id, record.tenant_id, record.user_object_id)
    return len(expired)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = asyncio.run(sweep_expired_artifacts())
    print(f"Swept {count} expired artifact(s).")
