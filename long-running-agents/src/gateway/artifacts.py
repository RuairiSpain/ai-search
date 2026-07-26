"""Artifact harvesting: copies upstream-transient files (a code
interpreter container lives ~1h) into the gateway's own blob store,
indexes them in gw_artifact, and mints short-lived download URLs.

docs/07-artifacts-and-code-interpreter.md §2, §3, §5. Scope note: this
harvests T2 code-interpreter citation artifacts, the fully-specified case
with a worked example in the docs. T2's artifact_url() still serves Session
Files downloads directly from that API (already identity-scoped upstream);
copying those into this same blob store too, per docs/07 §2 item 3
("one store, one contract"), is still open — see
docs/08-open-items-and-experiments.md.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobSasPermissions, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient

from gateway.auth.principal import Principal
from gateway.store.artifact_store import ArtifactStore
from gateway.upstream.base import ArtifactEvent

log = logging.getLogger(__name__)

FetchBytes = Callable[[dict], Awaitable[tuple[bytes, str]]]


class ArtifactHarvester:
    def __init__(self, *, blob_service: BlobServiceClient, container_name: str, artifacts: ArtifactStore):
        self._blob_service = blob_service
        self._container_name = container_name
        self._artifacts = artifacts

    def blob_key(
        self, *, app: str, principal_hash: str, context_id: str, task_id: str, artifact_id: str, name: str
    ) -> str:
        # Prefix layout is load-bearing — blob lifecycle policies match on
        # it, and a GDPR deletion request must be a single prefix delete
        # (docs/07 §2 item 2). Do not change casually.
        return f"artifacts/{app}/{principal_hash}/{context_id}/{task_id}/{artifact_id}-{name}"

    async def harvest(
        self,
        event: ArtifactEvent,
        *,
        app: str,
        principal: Principal,
        context_id: str,
        fetch_bytes: FetchBytes,
    ) -> ArtifactEvent:
        """Idempotent — safe to call twice for the same citation (a poll
        that re-observes it, or a reconnect). `artifact_id` is renamespaced
        to `{task_id}:{raw_id}` because the upstream's own id (a container
        file_id) is not guaranteed unique across tasks, while
        gw_artifact.artifact_id is a global primary key."""
        db_id = f"{event.task_id}:{event.artifact_id}"
        principal_hash = hashlib.sha256(principal.subject.encode()).hexdigest()[:16]

        await self._artifacts.ensure_pending(
            artifact_id=db_id,
            task_id=event.task_id,
            name=event.name,
            mime=event.mime,
            upstream_ref=event.upstream_ref,
        )

        if not event.upstream_ref:
            log.warning("artifact %s has no upstream_ref; nothing to fetch", db_id)
            return replace(event, artifact_id=db_id)

        try:
            data, mime = await fetch_bytes(event.upstream_ref)
        except Exception:
            log.exception("harvest failed for artifact %s", db_id)
            await self._artifacts.mark_failed(task_id=event.task_id, artifact_id=db_id)
            return replace(event, artifact_id=db_id)

        key = self.blob_key(
            app=app,
            principal_hash=principal_hash,
            context_id=context_id,
            task_id=event.task_id,
            artifact_id=event.artifact_id,
            name=event.name,
        )
        sha256 = hashlib.sha256(data).hexdigest()

        blob_client = self._blob_service.get_blob_client(self._container_name, key)
        try:
            await blob_client.upload_blob(data, overwrite=False, length=len(data))
        except ResourceExistsError:
            # Another poll (or replica) already harvested this exact
            # citation. Idempotent by design — not an error.
            pass

        await self._artifacts.mark_stored(
            task_id=event.task_id,
            artifact_id=db_id,
            blob_key=key,
            sha256=sha256,
            size_bytes=len(data),
        )

        uri = await self._sign_download_url(key)
        return ArtifactEvent(
            task_id=event.task_id,
            artifact_id=db_id,
            name=event.name,
            mime=mime or event.mime,
            sequence=event.sequence,
            uri=uri,
        )

    async def download_url(self, blob_key: str, *, ttl: timedelta = timedelta(hours=1)) -> str:
        return await self._sign_download_url(blob_key, ttl=ttl)

    async def _sign_download_url(self, blob_key: str, *, ttl: timedelta = timedelta(hours=1)) -> str:
        # User delegation SAS: Entra-backed, time-boxed, no account key
        # ever touches this process or leaves it (docs/07 §2 item 4 —
        # "never hand out raw blob URLs").
        start = datetime.now(UTC)
        expiry = start + ttl
        delegation_key = await self._blob_service.get_user_delegation_key(start, expiry)
        sas = generate_blob_sas(
            account_name=self._blob_service.account_name,
            container_name=self._container_name,
            blob_name=blob_key,
            user_delegation_key=delegation_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
            start=start,
        )
        blob_client = self._blob_service.get_blob_client(self._container_name, blob_key)
        return f"{blob_client.url}?{sas}"
