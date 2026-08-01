"""Exercises BlobStore.generate_download_url - the SAS-based download link that replaced the
broker: no proxy reads the blob and streams it back, so this is the only thing standing between
"anyone with the link" and the artifact. LocalDiskBlobStore's stand-in always runs; the
AzureBlobStore/Azurite cases (real generate_blob_sas, verified over real HTTP) are skipped
cleanly if Azurite isn't reachable, matching test_table_storage_backends.py's convention.
"""

import socket
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from long_duration_agent.storage.blob_store import AzureBlobStore, LocalDiskBlobStore


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


AZURITE_AVAILABLE = _port_open("127.0.0.1", 10000)

requires_azurite = pytest.mark.skipif(not AZURITE_AVAILABLE, reason="Azurite not running on 127.0.0.1:10000")

AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)


@pytest.mark.asyncio
async def test_local_disk_store_returns_a_fresh_url_on_every_call(tmp_path: Path):
    store = LocalDiskBlobStore(tmp_path)
    await store.upload_file(local_path=_write_temp_file(tmp_path, "hello"), blob_name="a/b.md")

    url1, expires1 = await store.generate_download_url("a/b.md", ttl_minutes=15)
    url2, expires2 = await store.generate_download_url("a/b.md", ttl_minutes=15)

    assert url1.startswith("file://")
    assert "a/b.md?t=" in url1
    assert url1 != url2  # never reused, same contract a real SAS's signature gives for free
    assert expires1 <= expires2


def _write_temp_file(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "source.md"
    p.write_text(content)
    return p


@requires_azurite
@pytest.mark.asyncio
async def test_azurite_backed_sas_url_is_fetchable_and_read_only():
    import httpx

    container = f"blobstoretest{uuid.uuid4().hex[:8]}"
    store = AzureBlobStore(container=container, connection_string=AZURITE_CONNECTION_STRING, create_container_if_missing=True)
    blob_name = "users/tenant-a/user-1/artifact.md"

    import tempfile

    tmp_file = Path(tempfile.mktemp())
    tmp_file.write_text("bilingual artifact content")
    await store.upload_file(local_path=tmp_file, blob_name=blob_name)

    url, expires_at = await store.generate_download_url(blob_name, ttl_minutes=15)

    parsed = parse_qs(urlparse(url).query)
    assert parsed["sp"] == ["r"]  # read-only permission, never write/delete
    assert "se" in parsed  # expiry is present and signed

    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        assert resp.status_code == 200
        assert resp.text == "bilingual artifact content"

        tampered = url[:-4] + "AAAA"
        tampered_resp = await client.get(tampered)
        assert tampered_resp.status_code == 403

    await store.delete(blob_name)


@requires_azurite
@pytest.mark.asyncio
async def test_azurite_backed_sas_url_respects_the_requested_ttl():
    from datetime import datetime, timedelta, timezone

    container = f"blobstoretest{uuid.uuid4().hex[:8]}"
    store = AzureBlobStore(container=container, connection_string=AZURITE_CONNECTION_STRING, create_container_if_missing=True)
    blob_name = "users/tenant-a/user-1/ttl.md"

    import tempfile

    tmp_file = Path(tempfile.mktemp())
    tmp_file.write_text("ttl test")
    await store.upload_file(local_path=tmp_file, blob_name=blob_name)

    before = datetime.now(timezone.utc)
    _url, expires_at = await store.generate_download_url(blob_name, ttl_minutes=5)
    after = datetime.now(timezone.utc)

    assert before + timedelta(minutes=5) <= expires_at <= after + timedelta(minutes=5, seconds=5)
