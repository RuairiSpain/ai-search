"""Durable artifact storage.

The hosted-agent local filesystem ($HOME/... equivalent, see
``workspace.py``) is scratch space only. This module is the durable side:
artifacts land here, and this is what a caller's browser downloads from
directly, via a freshly minted, short-lived SAS URL (``generate_download_url``)
- there is no broker or app-level proxy in front of it. In production this is
a Storage account reachable over the public internet (no private endpoint
required) but with anonymous blob access disabled - security comes entirely
from the SAS's signature and expiry, not network isolation. See
docs/architecture.md's "Public storage + SAS" section for the full rationale,
including how downloads get logged (Azure Storage diagnostic logs -> Log
Analytics, since the app itself never sees the actual read).

Two stand-ins are available so the pipeline runs without any real Azure
resources:

- ``LocalDiskBlobStore`` - pure Python, no external process, used as the
  default and in the test suite for speed and zero setup.
- ``AzureBlobStore`` pointed at Azurite (the official local Azure Storage
  emulator) - exercises the real ``azure-storage-blob`` SDK code path
  against a real Blob REST API implementation, so it's a much closer
  rehearsal of production than the local-disk stand-in. See
  docs/architecture.md for how to run it.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from ..config import get_settings


class BlobStore(Protocol):
    async def upload_file(self, *, local_path: Path, blob_name: str) -> int:
        """Uploads local_path's bytes to blob_name. Returns size in bytes."""
        ...

    async def open_read_stream(self, blob_name: str) -> BinaryIO:
        """Returns a binary file-like object positioned at the start of the blob."""
        ...

    async def delete(self, blob_name: str) -> None:
        ...

    async def generate_download_url(self, blob_name: str, *, ttl_minutes: int) -> tuple[str, datetime]:
        """Returns (url, expires_at) for a time-limited, read-only download link a caller's
        browser can fetch directly - no broker/proxy involved."""
        ...


class LocalDiskBlobStore:
    """Demo/test stand-in for a private Azure Storage account."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path_for(self, blob_name: str) -> Path:
        # blob_name is always server-generated (tenant/user/artifact ids), never raw user
        # input, but resolve+relative_to still guards against path traversal defensively.
        path = (self._root / blob_name).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise ValueError(f"Invalid blob name: {blob_name}")
        return path

    async def upload_file(self, *, local_path: Path, blob_name: str) -> int:
        dest = self._path_for(blob_name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, dest)
        return dest.stat().st_size

    async def open_read_stream(self, blob_name: str) -> BinaryIO:
        return open(self._path_for(blob_name), "rb")  # noqa: SIM115 - caller closes it

    async def delete(self, blob_name: str) -> None:
        self._path_for(blob_name).unlink(missing_ok=True)

    async def generate_download_url(self, blob_name: str, *, ttl_minutes: int) -> tuple[str, datetime]:
        # No real HTTP endpoint serves this locally - it's a demo/test stand-in only, so a
        # file:// URI (unfetchable by a remote browser) is enough to keep the pipeline's
        # contract uniform across backends. Real, browser-fetchable links only come from
        # AzureBlobStore (azurite/azure backends). A unique query string keeps this backend
        # honoring the same "always a fresh link, never reused" contract real SAS URLs get
        # from their signature - without it, two calls for the same blob would be identical.
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        return f"{self._path_for(blob_name).as_uri()}?t={uuid.uuid4().hex}", expires_at


class AzureBlobStore:
    """Real Azure Blob Storage, via the actual ``azure-storage-blob`` SDK client.

    Two ways to connect:

    - Production: ``account_url`` + Managed Identity. The storage account is reachable over
      the public internet (no private endpoint required - see infra/storage-public.bicep),
      but anonymous blob access is disabled; the only credential this process itself holds is
      a Managed Identity used to upload/delete blobs and, via the "Storage Blob Delegator" RBAC
      role, to request a User Delegation Key - which is what actually signs the short-lived SAS
      URLs handed out by ``generate_download_url``. No storage account key is ever used or
      needed for this path.
    - Local demo: ``connection_string`` pointed at Azurite. Azurite's well-known account key is
      a published emulator default, not a secret - it only ever authenticates against a local
      emulator, never a real Azure account. SAS URLs in this mode are signed with that account
      key instead of a user delegation key (Azurite's user-delegation-key support is
      inconsistent across versions; an account-key SAS exercises the same
      ``generate_blob_sas`` code path either way). ``create_container_if_missing`` is only
      meant for this path; a real account's container is provisioned by
      infra/storage-public.bicep instead.
    """

    def __init__(
        self,
        *,
        container: str,
        account_url: str | None = None,
        connection_string: str | None = None,
        create_container_if_missing: bool = False,
    ) -> None:
        # The async client (not azure.storage.blob.BlobServiceClient's sync twin) is required
        # here: this class's methods are awaited from request-handling code, and a sync
        # client's network calls would block the whole event loop for their duration.
        from azure.storage.blob.aio import BlobServiceClient

        if connection_string:
            self._service = BlobServiceClient.from_connection_string(connection_string)
            self._uses_account_key = True
        else:
            from azure.identity.aio import DefaultAzureCredential

            if not account_url:
                raise ValueError("AzureBlobStore requires either account_url or connection_string.")
            self._service = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
            self._uses_account_key = False

        self._container_name = container
        self._container = self._service.get_container_client(container)
        self._create_container_if_missing = create_container_if_missing
        self._container_ready = False
        self._delegation_key: Any = None
        self._delegation_key_expiry: datetime | None = None

    async def _ensure_container(self) -> None:
        if self._container_ready or not self._create_container_if_missing:
            return
        from azure.core.exceptions import ResourceExistsError

        try:
            await self._container.create_container()
        except ResourceExistsError:
            pass
        self._container_ready = True

    async def upload_file(self, *, local_path: Path, blob_name: str) -> int:
        await self._ensure_container()
        size = local_path.stat().st_size
        with open(local_path, "rb") as fh:
            await self._container.upload_blob(name=blob_name, data=fh, overwrite=True)
        return size

    async def open_read_stream(self, blob_name: str) -> BinaryIO:
        import io

        from azure.core.exceptions import ResourceNotFoundError

        try:
            downloader = await self._container.download_blob(blob_name)
            data = await downloader.readall()
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(blob_name) from exc
        return io.BytesIO(data)

    async def delete(self, blob_name: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            await self._container.delete_blob(blob_name)
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(blob_name) from exc

    async def generate_download_url(self, blob_name: str, *, ttl_minutes: int) -> tuple[str, datetime]:
        from urllib.parse import quote

        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        now = datetime.now(timezone.utc)
        expiry = now + timedelta(minutes=ttl_minutes)
        permission = BlobSasPermissions(read=True)

        if self._uses_account_key:
            sas_token = generate_blob_sas(
                account_name=self._service.account_name,
                container_name=self._container_name,
                blob_name=blob_name,
                account_key=self._service.credential.account_key,
                permission=permission,
                expiry=expiry,
                start=now,
            )
        else:
            delegation_key = await self._get_user_delegation_key(expiry)
            sas_token = generate_blob_sas(
                account_name=self._service.account_name,
                container_name=self._container_name,
                blob_name=blob_name,
                user_delegation_key=delegation_key,
                permission=permission,
                expiry=expiry,
                start=now,
            )

        blob_path = "/".join(quote(part) for part in blob_name.split("/"))
        return f"{self._container.url}/{blob_path}?{sas_token}", expiry

    async def _get_user_delegation_key(self, sas_expiry: datetime) -> Any:
        # A user delegation key is itself valid for a time range and can sign any number of
        # SAS tokens within it, so it's cached and reused rather than requested fresh per
        # download link - one control-plane call per hour (at most) instead of one per link.
        now = datetime.now(timezone.utc)
        if (
            self._delegation_key is None
            or self._delegation_key_expiry is None
            or self._delegation_key_expiry <= sas_expiry
        ):
            key_expiry = max(now + timedelta(hours=1), sas_expiry)
            self._delegation_key = await self._service.get_user_delegation_key(now, key_expiry)
            self._delegation_key_expiry = key_expiry
        return self._delegation_key


_STORE: BlobStore | None = None


def get_blob_store() -> BlobStore:
    global _STORE
    if _STORE is not None:
        return _STORE
    settings = get_settings()
    backend = settings.lda_storage_backend
    if backend == "azure":
        _STORE = AzureBlobStore(
            account_url=settings.azure_storage_account_url,
            container=settings.azure_storage_container,
        )
    elif backend == "azurite":
        _STORE = AzureBlobStore(
            connection_string=settings.azurite_connection_string,
            container=settings.azure_storage_container,
            create_container_if_missing=True,
        )
    else:
        _STORE = LocalDiskBlobStore(settings.local_storage_root)
    return _STORE


def reset_blob_store_cache() -> None:
    """Test helper: forces the next get_blob_store() call to rebuild from current settings."""
    global _STORE
    _STORE = None
