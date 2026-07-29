"""Durable artifact storage.

The hosted-agent local filesystem ($HOME/... equivalent, see
``workspace.py``) is scratch space only. This module is the durable side:
artifacts land here, and this is what the Artifact Broker API reads from
when it streams a download. In production this is a private Azure Storage
account (public network access disabled, accessed only via the broker's
managed identity - see docs/architecture.md).

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
from pathlib import Path
from typing import BinaryIO, Protocol

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


class AzureBlobStore:
    """Real Azure Blob Storage, via the actual ``azure-storage-blob`` SDK client.

    Two ways to connect:

    - Production: ``account_url`` + Managed Identity (no account keys, no SAS).
      Requires public network access disabled + a private endpoint on the
      storage account (see infra/storage-private.bicep); the account is never
      reachable directly from a browser. Only this process's managed identity
      (or a developer's `az login` credential locally) can read/write it, which
      is exactly why the Artifact Broker API - not a handed-out SAS URL - is
      the thing users' browsers talk to.
    - Local demo: ``connection_string`` pointed at Azurite. Azurite's
      well-known account key is a published emulator default, not a secret -
      it only ever authenticates against a local emulator, never a real
      Azure account. ``create_container_if_missing`` is only meant for this
      path; a real account's container is provisioned by
      infra/storage-private.bicep instead.
    """

    def __init__(
        self,
        *,
        container: str,
        account_url: str | None = None,
        connection_string: str | None = None,
        create_container_if_missing: bool = False,
    ) -> None:
        from azure.storage.blob import ContainerClient

        if connection_string:
            self._container = ContainerClient.from_connection_string(
                connection_string, container_name=container
            )
        else:
            from azure.identity import DefaultAzureCredential

            if not account_url:
                raise ValueError("AzureBlobStore requires either account_url or connection_string.")
            self._container = ContainerClient(
                account_url=account_url,
                container_name=container,
                credential=DefaultAzureCredential(),
            )

        if create_container_if_missing:
            from azure.core.exceptions import ResourceExistsError

            try:
                self._container.create_container()
            except ResourceExistsError:
                pass

    async def upload_file(self, *, local_path: Path, blob_name: str) -> int:
        size = local_path.stat().st_size
        with open(local_path, "rb") as fh:
            self._container.upload_blob(name=blob_name, data=fh, overwrite=True)
        return size

    async def open_read_stream(self, blob_name: str) -> BinaryIO:
        import io

        downloader = self._container.download_blob(blob_name)
        return io.BytesIO(downloader.readall())

    async def delete(self, blob_name: str) -> None:
        self._container.delete_blob(blob_name)


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
