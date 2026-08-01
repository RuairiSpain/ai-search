"""Offline tests for DurableAdapter.fetch_artifact_bytes() -- the T3
artifact-harvesting hook that previously didn't exist at all, so
getattr(adapter, "fetch_artifact_bytes", None) always returned None and a
T3 artifact with no pre-set `uri` was silently dropped by
_follow_and_relay() (docs/08).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from gateway.upstream.durable import DurableAdapter


class _FakeEventSource:
    async def events_after(self, task_id, from_sequence):
        return []

    async def wait_for_new_event(self, task_id, timeout_s):
        return False


@pytest.mark.asyncio
async def test_fetch_artifact_bytes_downloads_from_upstream_ref_url():
    adapter = DurableAdapter(
        instances=["https://t3.internal"], health_path="/healthz", event_source=_FakeEventSource()
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://t3-artifacts.internal/report.pdf"
        return httpx.Response(200, content=b"%PDF-fake-bytes", headers={"content-type": "application/pdf"})

    fake_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("gateway.upstream.durable.httpx.AsyncClient", return_value=fake_client):
        data, mime = await adapter.fetch_artifact_bytes(
            {"download_url": "https://t3-artifacts.internal/report.pdf"}
        )

    assert data == b"%PDF-fake-bytes"
    assert mime == "application/pdf"


@pytest.mark.asyncio
async def test_fetch_artifact_bytes_requires_download_url():
    adapter = DurableAdapter(
        instances=["https://t3.internal"], health_path="/healthz", event_source=_FakeEventSource()
    )
    with pytest.raises(RuntimeError, match="download_url"):
        await adapter.fetch_artifact_bytes({"container_id": "not-what-t3-uses"})


@pytest.mark.asyncio
async def test_follow_and_relay_harvests_t3_artifact_via_fetch_hook():
    """End-to-end through the same code path _follow_and_relay() uses:
    getattr(adapter, "fetch_artifact_bytes", None) now finds a real method
    on DurableAdapter and the harvester copies the bytes through."""
    from gateway.artifacts import ArtifactHarvester
    from gateway.auth.principal import Principal
    from gateway.upstream.base import ArtifactEvent

    class _FakeBlobClient:
        def __init__(self, url):
            self.url = url

        async def upload_blob(self, data, overwrite, length):
            return None

    class _FakeBlobService:
        account_name = "fakeaccount"

        def get_blob_client(self, container, key):
            return _FakeBlobClient(f"https://fakeaccount.blob.core.windows.net/{container}/{key}")

        async def get_user_delegation_key(self, start, expiry):
            return AsyncMock()

    class _FakeArtifactStore:
        async def ensure_pending(self, **kwargs):
            return None

        async def mark_stored(self, **kwargs):
            return None

    adapter = DurableAdapter(
        instances=["https://t3.internal"], health_path="/healthz", event_source=_FakeEventSource()
    )
    harvester = ArtifactHarvester(
        blob_service=_FakeBlobService(), container_name="artifacts", artifacts=_FakeArtifactStore()
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"csv,1,2", headers={"content-type": "text/csv"})

    fake_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with (
        patch("gateway.upstream.durable.httpx.AsyncClient", return_value=fake_client),
        patch(
            "gateway.artifacts.generate_blob_sas",
            return_value="sig=fake",
        ),
    ):
        event = ArtifactEvent(
            task_id="task_1",
            artifact_id="file_1",
            name="data.csv",
            mime="text/csv",
            sequence=1,
            uri=None,
            upstream_ref={"download_url": "https://t3-artifacts.internal/data.csv"},
        )
        fetch_bytes = adapter.fetch_artifact_bytes
        harvested = await harvester.harvest(
            event,
            app="deep-research",
            principal=Principal(subject="t3.alice", tenant="t3"),
            context_id="ctx_1",
            fetch_bytes=fetch_bytes,
        )

    assert harvested.uri is not None
    assert "artifacts/deep-research" in harvested.uri
