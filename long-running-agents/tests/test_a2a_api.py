"""End-to-end wiring test for the A2A surface: message/send -> tasks/get
-> SSE stream -> tasks/cancel, through the real FastAPI router and a real
Postgres, with a fake adapter standing in for Foundry/T3. This is the
layer the store-level unit tests can't catch — signature drift between
build_router() and main.py, or between the adapter Protocol and what the
router actually calls.

Deliberately does not exercise artifact harvesting's blob/SAS step (the
fake adapter has no `fetch_artifact_bytes`, so the harvester is skipped) —
that needs a real Storage account, same reasoning as
docs/05-tier2-hosted-agents.md "isolation ... invisible in dev" for why
some things can only be verified against real Azure.
"""
from __future__ import annotations

import json
import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from gateway.api.a2a import build_router
from gateway.artifacts import ArtifactHarvester
from gateway.auth.principal import EntraValidator
from gateway.config import AppConfig, AuthConfig, GatewayConfig, UpstreamConfig
from gateway.store.artifact_store import ArtifactStore
from gateway.store.context_store import ContextStore
from gateway.store.task_store import TaskStore
from gateway.upstream.base import (
    ArtifactEvent,
    Capabilities,
    ProgressFidelity,
    StatusEvent,
    Submission,
    TaskState,
    UpstreamRef,
)

TENANT_ID = "22222222-2222-2222-2222-222222222222"
AUDIENCE = "api://a2a-gateway"


class FakeAdapter:
    capabilities = Capabilities(
        progress=ProgressFidelity.COARSE, push=False, artifacts=True, input_required=False, cancel=True
    )

    def __init__(self):
        self.cancelled: list[UpstreamRef] = []

    async def submit(self, *, app, principal, ref, text, blocking, budget_ms):
        # A fresh id per call — this fake stands in for multiple test
        # functions sharing one persistent local Postgres with no
        # per-test teardown, so a hardcoded id would collide across tests.
        return Submission(
            task_id=f"task_fake_{uuid4().hex[:8]}",
            context_id="ignored-by-router",
            state=TaskState.WORKING,
            ref=UpstreamRef(run_id="upstream_run_1"),
        )

    async def follow(self, ref, *, task_id, principal, from_sequence=0):
        yield StatusEvent(task_id=task_id, state=TaskState.WORKING, sequence=from_sequence + 1)
        yield ArtifactEvent(
            task_id=task_id,
            artifact_id="file_1",
            name="out.txt",
            mime="text/plain",
            sequence=from_sequence + 2,
            upstream_ref={"container_id": "c1", "file_id": "file_1"},
        )
        yield StatusEvent(
            task_id=task_id, state=TaskState.COMPLETED, sequence=from_sequence + 3, final=True
        )

    async def resume(self, ref, *, principal, text):
        raise NotImplementedError

    async def steer(self, ref, *, principal, text):
        raise NotImplementedError

    async def cancel(self, ref, *, principal):
        self.cancelled.append(ref)

    async def artifact_url(self, ref, artifact_id, *, principal):
        raise NotImplementedError

    async def health(self):
        return True


class FakeRegistry:
    def __init__(self, adapter, validator):
        self._adapter = adapter
        self.validator = validator

    def adapter_for_app(self, app_name):
        return self._adapter


def _bearer_token(rsa_key) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "oid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
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
def app_and_adapter(pg_pool, rsa_key):
    config = GatewayConfig(
        auth=AuthConfig(tenant_id=TENANT_ID, audience=AUDIENCE),
        apps=[AppConfig(name="ticket-triage", tier="t1", upstream="t1-up")],
        upstreams=[UpstreamConfig(id="t1-up", tier="t1", project_endpoint="https://example", agent_name="a")],
    )
    validator = EntraValidator(tenant_id=TENANT_ID, audience=AUDIENCE)

    class _FakeSigningKey:
        key = rsa_key.public_key()

    validator._jwks.get_signing_key_from_jwt = lambda token: _FakeSigningKey()  # type: ignore[method-assign]

    adapter = FakeAdapter()
    registry = FakeRegistry(adapter, validator)
    contexts = ContextStore(pg_pool)
    tasks = TaskStore(pg_pool)
    artifacts = ArtifactStore(pg_pool)
    harvester = ArtifactHarvester(blob_service=None, container_name="artifacts", artifacts=artifacts)  # type: ignore[arg-type]

    fastapi_app = FastAPI()
    fastapi_app.include_router(build_router(config, registry, contexts, tasks, artifacts, harvester))
    return fastapi_app, adapter


@pytest.mark.asyncio
async def test_message_send_then_get_then_cancel(app_and_adapter, rsa_key):
    fastapi_app, adapter = app_and_adapter
    token = _bearer_token(rsa_key)
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        # gw_inbound_message.message_id is a PRIMARY KEY in a persistent
        # local Postgres with no per-test truncation — a literal id would
        # be "already seen" on the second `make test` run.
        message_id = f"m-{uuid4().hex[:8]}"
        send_resp = await client.post(
            "/apps/ticket-triage/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {
                    "message": {"messageId": message_id, "role": "user", "parts": [{"kind": "text", "text": "hi"}]}
                },
            },
        )
        assert send_resp.status_code == 200, send_resp.text
        task = send_resp.json()["result"]
        assert task["id"].startswith("task_fake_")
        assert task["status"]["state"] == "working"

        # Retry with the same messageId must dedupe, not resubmit.
        retry_resp = await client.post(
            "/apps/ticket-triage/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": "2",
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": message_id,
                        "role": "user",
                        "parts": [{"kind": "text", "text": "hi"}],
                        "contextId": task["contextId"],
                    }
                },
            },
        )
        assert retry_resp.json()["result"] == {"deduped": True}

        get_resp = await client.post(
            "/apps/ticket-triage/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": "3", "method": "tasks/get", "params": {"id": task["id"]}},
        )
        assert get_resp.json()["result"]["status"]["state"] == "working"

        # A different principal must not be able to read this task.
        other_headers = {"Authorization": f"Bearer {_other_principal_token(rsa_key)}"}
        forbidden = await client.post(
            "/apps/ticket-triage/",
            headers=other_headers,
            json={"jsonrpc": "2.0", "id": "4", "method": "tasks/get", "params": {"id": task["id"]}},
        )
        assert forbidden.status_code == 404

        cancel_resp = await client.post(
            "/apps/ticket-triage/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": "5", "method": "tasks/cancel", "params": {"id": task["id"]}},
        )
        assert cancel_resp.status_code == 200
        assert len(adapter.cancelled) == 1


@pytest.mark.asyncio
async def test_sse_stream_persists_and_forwards_events(app_and_adapter, rsa_key):
    fastapi_app, _adapter = app_and_adapter
    token = _bearer_token(rsa_key)
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        send_resp = await client.post(
            "/apps/ticket-triage/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": f"m-{uuid4().hex[:8]}",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "hi"}],
                    }
                },
            },
        )
        task_id = send_resp.json()["result"]["id"]

        events = []
        async with client.stream(
            "GET", f"/apps/ticket-triage/tasks/{task_id}/stream", headers=headers
        ) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    events.append(json.loads(line[len("data: ") :]))
                if line == "data: [DONE]":
                    break

        kinds = [e["kind"] for e in events]
        assert kinds == ["status", "artifact", "status"]
        assert events[-1]["state"] == "completed"
        assert events[-1]["final"] is True
        # upstream_ref is internal harvester metadata and must never reach
        # the client (gateway.api.a2a.sse_event_stream strips it).
        assert "upstream_ref" not in events[1]

        # tasks/get should now reflect the terminal state persisted by the
        # stream's append_event calls.
        get_resp = await client.post(
            "/apps/ticket-triage/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": "2", "method": "tasks/get", "params": {"id": task_id}},
        )
        assert get_resp.json()["result"]["status"]["state"] == "completed"


def _other_principal_token(rsa_key) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "oid": "ffffffff-ffff-ffff-ffff-ffffffffffff",
            "tid": TENANT_ID,
            "aud": AUDIENCE,
            "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
            "exp": now + 300,
            "iat": now,
        },
        rsa_key,
        algorithm="RS256",
    )
