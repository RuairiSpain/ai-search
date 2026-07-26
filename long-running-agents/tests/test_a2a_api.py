"""End-to-end wiring test for the A2A surface built on a2a-sdk:
SendMessage -> GetTask -> CancelTask, through the real mounted FastAPI
routes and a real Postgres, with a fake adapter standing in for
Foundry/T3. This is the layer store-level unit tests can't catch --
signature drift between mount_app()/GatewayAgentExecutor and what
a2a-sdk's DefaultRequestHandlerV2 actually calls.

Deliberately does not exercise artifact harvesting's blob/SAS step (the
fake adapter has no `fetch_artifact_bytes`, so the harvester is skipped) --
that needs a real Storage account, same reasoning as
docs/05-tier2-hosted-agents.md "isolation ... invisible in dev" for why
some things can only be verified against real Azure.
"""
from __future__ import annotations

import asyncio
import base64
import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from gateway.a2a_server.app import mount_app
from gateway.artifacts import ArtifactHarvester
from gateway.auth.principal import EntraValidator
from gateway.config import AppConfig, CardCapabilities, CardConfig
from gateway.store.artifact_store import ArtifactStore
from gateway.store.context_store import ContextStore
from gateway.store.task_store import TaskStore
from gateway.upstream.base import (
    Capabilities,
    InboundFile,
    ProgressFidelity,
    StatusEvent,
    Submission,
    TaskState,
    UpstreamRef,
)

TENANT_ID = "22222222-2222-2222-2222-222222222222"
AUDIENCE = "api://a2a-gateway"


class FakeAdapter:
    """Stands in for FoundryHostedAdapter/DurableAdapter. follow() blocks
    on an asyncio.Event after the first WORKING status so tests can
    exercise CancelTask against a genuinely in-flight task, then converges
    to CANCELED or COMPLETED depending on whether cancel() was called --
    mirroring D7 "never optimistic": the state only actually flips once
    follow() observes the upstream confirm it."""

    capabilities = Capabilities(
        progress=ProgressFidelity.COARSE, push=False, artifacts=True, input_required=False, cancel=True
    )

    def __init__(self):
        self.cancelled: list[UpstreamRef] = []
        self.received_files: list[InboundFile] = []
        self._release = asyncio.Event()

    async def submit(self, *, app, principal, ref, text, files, blocking, budget_ms):
        # A fresh id per call -- this fake stands in for multiple test
        # functions sharing one persistent local Postgres with no
        # per-test teardown, so a hardcoded id would collide across tests.
        self.received_files = list(files)
        return Submission(
            task_id=f"task_fake_{uuid4().hex[:8]}",
            context_id="ignored-by-executor",
            state=TaskState.WORKING,
            ref=UpstreamRef(run_id=f"run_{uuid4().hex[:8]}"),
        )

    async def follow(self, ref, *, task_id, principal, from_sequence=0):
        seq = from_sequence + 1
        yield StatusEvent(task_id=task_id, state=TaskState.WORKING, sequence=seq)
        await self._release.wait()
        seq += 1
        final_state = TaskState.CANCELED if ref in self.cancelled else TaskState.COMPLETED
        yield StatusEvent(task_id=task_id, state=final_state, sequence=seq, final=True)

    async def resume(self, ref, *, principal, text, files):
        raise NotImplementedError

    async def steer(self, ref, *, principal, text):
        raise NotImplementedError

    async def cancel(self, ref, *, principal):
        self.cancelled.append(ref)
        self._release.set()

    async def artifact_url(self, ref, artifact_id, *, principal):
        raise NotImplementedError

    async def health(self):
        return True


def _headers(token: str) -> dict:
    # A2A-Version is required: a2a-sdk's own validate_version defaults a
    # request with no version header to protocol 0.3, which this handler
    # (built for 1.0) then rejects.
    return {"Authorization": f"Bearer {token}", "A2A-Version": "1.0"}


def _bearer_token(rsa_key, *, oid: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "oid": oid,
            "tid": TENANT_ID,
            "aud": AUDIENCE,
            "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
            "exp": now + 300,
            "iat": now,
        },
        rsa_key,
        algorithm="RS256",
    )


@pytest.fixture()
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def validator(rsa_key):
    v = EntraValidator(tenant_id=TENANT_ID, audience=AUDIENCE)

    class _FakeSigningKey:
        key = rsa_key.public_key()

    v._jwks.get_signing_key_from_jwt = lambda token: _FakeSigningKey()  # type: ignore[method-assign]
    return v


@pytest.fixture()
def app_and_adapter(pg_pool, validator):
    app_cfg = AppConfig(
        name="ticket-triage",
        tier="t2",
        upstream="t2-up",
        card=CardConfig(capabilities=CardCapabilities(streaming=False)),
    )
    adapter = FakeAdapter()
    contexts = ContextStore(pg_pool)
    tasks = TaskStore(pg_pool)
    artifacts = ArtifactStore(pg_pool)
    harvester = ArtifactHarvester(blob_service=None, container_name="artifacts", artifacts=artifacts)  # type: ignore[arg-type]

    fastapi_app = FastAPI()
    mount_app(
        fastapi_app,
        app_cfg=app_cfg,
        adapter=adapter,
        validator=validator,
        contexts=contexts,
        tasks=tasks,
        artifacts=artifacts,
        harvester=harvester,
    )
    return fastapi_app, adapter


async def _rpc(client: AsyncClient, method: str, params: dict, *, headers: dict, req_id: str = "1") -> dict:
    resp = await client.post(
        "/apps/ticket-triage/",
        headers=headers,
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_send_message_blocks_until_terminal_then_get(app_and_adapter, rsa_key):
    fastapi_app, adapter = app_and_adapter
    headers = _headers(_bearer_token(rsa_key))
    # Nothing in this test calls cancel(), and follow() blocks on
    # self._release after the first WORKING event -- release it up front
    # so the default (blocking) SendMessage call actually completes.
    adapter._release.set()

    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        message_id = f"m-{uuid4().hex[:8]}"
        body = await _rpc(
            client,
            "SendMessage",
            {"message": {"messageId": message_id, "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
            headers=headers,
        )
        task = body["result"]["task"]
        # The task id is minted by a2a-sdk's own RequestContextBuilder
        # (a fresh UUID per request, since the message omits taskId) --
        # not by the upstream adapter's Submission.task_id, which the
        # executor never surfaces as the gateway's own task identity.
        assert task["id"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"

        get_body = await _rpc(client, "GetTask", {"id": task["id"]}, headers=headers, req_id="2")
        assert get_body["result"]["status"]["state"] == "TASK_STATE_COMPLETED"

        # A different principal must not be able to read this task -- 404
        # semantics (an A2A "not found" error), never a 403 (D1).
        other_headers = _headers(_bearer_token(rsa_key, oid="ffffffff-ffff-ffff-ffff-ffffffffffff"))
        forbidden = await _rpc(client, "GetTask", {"id": task["id"]}, headers=other_headers, req_id="3")
        assert forbidden["error"]["code"] == -32001  # TASK_NOT_FOUND


@pytest.mark.asyncio
async def test_send_message_with_file_parts_extracts_and_forwards_files(app_and_adapter, rsa_key):
    fastapi_app, adapter = app_and_adapter
    headers = _headers(_bearer_token(rsa_key))
    adapter._release.set()

    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        message_id = f"m-{uuid4().hex[:8]}"
        body = await _rpc(
            client,
            "SendMessage",
            {
                "message": {
                    "messageId": message_id,
                    "role": "ROLE_USER",
                    "parts": [
                        {"text": "see attached"},
                        {
                            "raw": base64.b64encode(b"csv,data\n1,2").decode(),
                            "filename": "data.csv",
                            "mediaType": "text/csv",
                        },
                        {
                            "url": "https://example.com/report.pdf",
                            "filename": "report.pdf",
                            "mediaType": "application/pdf",
                        },
                    ],
                }
            },
            headers=headers,
        )
        assert body["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"

        assert len(adapter.received_files) == 2
        by_name = {f.name: f for f in adapter.received_files}
        assert by_name["data.csv"].mime == "text/csv"
        assert by_name["data.csv"].data == b"csv,data\n1,2"
        assert by_name["data.csv"].url is None
        assert by_name["report.pdf"].mime == "application/pdf"
        assert by_name["report.pdf"].url == "https://example.com/report.pdf"
        assert by_name["report.pdf"].data is None


@pytest.mark.asyncio
async def test_cancel_relays_to_adapter_and_state_converges(app_and_adapter, rsa_key):
    fastapi_app, adapter = app_and_adapter
    headers = _headers(_bearer_token(rsa_key))

    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        message_id = f"m-{uuid4().hex[:8]}"
        send_body = await _rpc(
            client,
            "SendMessage",
            {
                "message": {"messageId": message_id, "role": "ROLE_USER", "parts": [{"text": "hi"}]},
                "configuration": {"returnImmediately": True},
            },
            headers=headers,
        )
        task = send_body["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_WORKING"

        cancel_body = await _rpc(client, "CancelTask", {"id": task["id"]}, headers=headers, req_id="2")
        assert cancel_body["result"]["id"] == task["id"]
        assert len(adapter.cancelled) == 1

        # D7: never optimistic -- state only flips once follow() observes
        # the upstream confirm it, which happens asynchronously here.
        for _ in range(50):
            get_body = await _rpc(client, "GetTask", {"id": task["id"]}, headers=headers, req_id="3")
            if get_body["result"]["status"]["state"] == "TASK_STATE_CANCELED":
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("task never converged to TASK_STATE_CANCELED")
