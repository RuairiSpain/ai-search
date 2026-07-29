"""Durable artifact storage.

The hosted-agent local filesystem ($HOME/... equivalent, see
``workspace.py``) is scratch space only. This module is the durable side:
artifacts land here, and this is what the Artifact Broker API reads from
when it streams a download. In production this is a private Azure Storage
account (public network access disabled, accessed only via the broker's
managed identity - see docs/architecture.md). For local demo/test runs,
``LocalDiskBlobStore`` stands in for that account so the whole pipeline
runs without any Azure resources.
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
    """Private Azure Blob Storage, authenticated via Managed Identity (no account keys, no SAS).

    Requires public network access disabled + a private endpoint on the
    storage account (see infra/storage-private.bicep); the account is never
    reachable directly from a browser. Only this process's managed identity
    (or a developer's `az login` credential locally) can read/write it, which
    is exactly why the Artifact Broker API - not a handed-out SAS URL - is the
    thing users' browsers talk to.
    """

    def __init__(self, account_url: str, container: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        self._container = ContainerClient(
            account_url=account_url,
            container_name=container,
            credential=DefaultAzureCredential(),
        )

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
    if settings.lda_storage_backend == "azure":
        _STORE = AzureBlobStore(settings.azure_storage_account_url, settings.azure_storage_container)
    else:
        _STORE = LocalDiskBlobStore(settings.local_storage_root)
    return _STORE


def reset_blob_store_cache() -> None:
    """Test helper: forces the next get_blob_store() call to rebuild from current settings."""
    global _STORE
    _STORE = None
