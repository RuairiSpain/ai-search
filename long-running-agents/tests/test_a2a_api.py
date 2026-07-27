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

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from gateway.a2a_server.app import mount_app
from gateway.a2a_server.push_config import GatewayPushConfigStore
from gateway.artifacts import ArtifactHarvester
from gateway.auth.principal import EntraValidator
from gateway.config import AppConfig, CardCapabilities, CardConfig
from gateway.store.artifact_store import ArtifactStore
from gateway.store.context_store import ContextStore
from gateway.store.interjection_store import InterjectionStore
from gateway.store.message_store import MessageStore
from gateway.store.task_store import TaskStore
from gateway.upstream.base import (
    Capabilities,
    InboundFile,
    ProgressFidelity,
    StatusEvent,
    SteeringMode,
    SteerResult,
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
        progress=ProgressFidelity.COARSE,
        push=False,
        artifacts=True,
        input_required=False,
        cancel=True,
        steering=SteeringMode.CHECKPOINT,
    )

    def __init__(self):
        self.cancelled: list[UpstreamRef] = []
        self.received_files: list[InboundFile] = []
        self.steered: list[str] = []
        self._release = asyncio.Event()
        # Opt-in narration for history/status.message tests (docs/08 item
        # 17): unset by default, so every existing test's event count and
        # sequencing is unchanged. narration_steps yields one extra WORKING
        # event per entry, each carrying `detail`, before the release wait;
        # final_detail sets `detail` on the terminal event.
        self.narration_steps: list[str] = []
        self.final_detail: str | None = None

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
        for step_detail in self.narration_steps:
            seq += 1
            yield StatusEvent(task_id=task_id, state=TaskState.WORKING, sequence=seq, detail=step_detail)
        await self._release.wait()
        seq += 1
        final_state = TaskState.CANCELED if ref in self.cancelled else TaskState.COMPLETED
        yield StatusEvent(
            task_id=task_id, state=final_state, sequence=seq, final=True, detail=self.final_detail
        )

    async def resume(self, ref, *, principal, text, files):
        raise NotImplementedError

    async def steer(self, ref, *, principal, text):
        self.steered.append(text)
        return SteerResult(outcome="accepted", applies_at="next step")

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
async def app_and_adapter(pg_pool, validator):
    app_cfg = AppConfig(
        name="ticket-triage",
        tier="t2",
        upstream="t2-up",
        card=CardConfig(
            capabilities=CardCapabilities(streaming=False, pushNotifications=True)
        ),
    )
    adapter = FakeAdapter()
    contexts = ContextStore(pg_pool)
    tasks = TaskStore(pg_pool)
    artifacts = ArtifactStore(pg_pool)
    messages = MessageStore(pg_pool)
    interjections = InterjectionStore(pg_pool)
    push_config_store = GatewayPushConfigStore(pg_pool, allowlist=["push.example.com"])
    pushed: list[httpx.Request] = []

    def _push_handler(request: httpx.Request) -> httpx.Response:
        pushed.append(request)
        return httpx.Response(200)

    push_http_client = httpx.AsyncClient(transport=httpx.MockTransport(_push_handler))
    harvester = ArtifactHarvester(blob_service=None, container_name="artifacts", artifacts=artifacts)  # type: ignore[arg-type]

    fastapi_app = FastAPI()
    request_handler = mount_app(
        fastapi_app,
        app_cfg=app_cfg,
        adapter=adapter,
        validator=validator,
        contexts=contexts,
        tasks=tasks,
        artifacts=artifacts,
        messages=messages,
        harvester=harvester,
        interjections=interjections,
        push_config_store=push_config_store,
        push_http_client=push_http_client,
    )
    try:
        yield fastapi_app, adapter, pushed
    finally:
        # Drains in-flight ActiveTask producer/consumer background tasks
        # before pg_pool's own finalizer closes the pool underneath them --
        # without this, a task still processing its final event after the
        # test's last assertion races pool teardown ("pool is closing").
        await request_handler.aclose()
        await push_http_client.aclose()


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
    fastapi_app, adapter, _pushed = app_and_adapter
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
        # No "result" object at all on the error path -- get() returns None
        # before _project_messages() (or anything else) ever runs, so
        # there's no history to leak here by construction.
        assert "result" not in forbidden


@pytest.mark.asyncio
async def test_get_task_returns_history_and_current_answer_not_buried_in_it(app_and_adapter, rsa_key):
    """The direct regression test for docs/08 item 17: a completed task's
    answer must land in status.message, not only ever be reachable as the
    last element of history (or nowhere at all, which was the actual bug)."""
    fastapi_app, adapter, _pushed = app_and_adapter
    headers = _headers(_bearer_token(rsa_key))
    adapter.final_detail = "Hello from the fake agent"
    adapter._release.set()

    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        message_id = f"m-{uuid4().hex[:8]}"
        body = await _rpc(
            client,
            "SendMessage",
            {"message": {"messageId": message_id, "role": "ROLE_USER", "parts": [{"text": "what's the status?"}]}},
            headers=headers,
        )
        task = body["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert task["status"]["message"]["parts"][0]["text"] == "Hello from the fake agent"

        history_texts = [
            part["text"] for m in task.get("history", []) for part in m.get("parts", []) if "text" in part
        ]
        assert "what's the status?" in history_texts
        # The answer is in status.message -- it must not also show up as
        # the tail of history (it was never demoted; there's no later
        # status update to demote it).
        assert "Hello from the fake agent" not in history_texts

        get_body = await _rpc(client, "GetTask", {"id": task["id"]}, headers=headers, req_id="2")
        get_task = get_body["result"]
        assert get_task["status"]["message"]["parts"][0]["text"] == "Hello from the fake agent"
        assert "what's the status?" in [
            p["text"] for m in get_task.get("history", []) for p in m.get("parts", []) if "text" in p
        ]


@pytest.mark.asyncio
async def test_status_message_gets_demoted_to_history_on_a_later_update(app_and_adapter, rsa_key):
    fastapi_app, adapter, _pushed = app_and_adapter
    headers = _headers(_bearer_token(rsa_key))
    adapter.narration_steps = ["first narration", "second narration"]
    # Deliberately NOT releasing -- the task must still be WORKING when
    # GetTask is polled, so the demotion is observed mid-run, not after
    # completion (docs/08 item 17's "current vs. superseded" distinction
    # only matters while there's a "later" update to demote the earlier one).

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
        task_id = send_body["result"]["task"]["id"]

        try:
            for _ in range(100):
                get_body = await _rpc(client, "GetTask", {"id": task_id}, headers=headers, req_id="2")
                result = get_body["result"]
                message = result["status"].get("message")
                if message and message["parts"][0]["text"] == "second narration":
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("task never reached the second narration step")

            assert result["status"]["state"] == "TASK_STATE_WORKING"
            history_texts = [
                p["text"] for m in result.get("history", []) for p in m.get("parts", []) if "text" in p
            ]
            assert "first narration" in history_texts
            assert "second narration" not in history_texts  # current, not yet demoted
        finally:
            # Always release, pass or fail -- otherwise a failure here
            # leaves the background follow() loop blocked on _release
            # forever, hanging the fixture's aclose() teardown.
            adapter._release.set()


@pytest.mark.asyncio
async def test_send_message_with_file_parts_extracts_and_forwards_files(app_and_adapter, rsa_key):
    fastapi_app, adapter, _pushed = app_and_adapter
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
async def test_push_notification_config_delivers_on_completion(app_and_adapter, rsa_key):
    fastapi_app, adapter, pushed = app_and_adapter
    headers = _headers(_bearer_token(rsa_key))
    # Deliberately NOT releasing yet -- the config must be registered while
    # the task is still genuinely working, or there's nothing left to
    # notify about by the time it exists.

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
        task_id = send_body["result"]["task"]["id"]

        blocked = await _rpc(
            client,
            "CreateTaskPushNotificationConfig",
            {"taskId": task_id, "url": "https://not-allowlisted.evil.example/cb"},
            headers=headers,
            req_id="2",
        )
        assert "error" in blocked, blocked  # L023: SSRF allowlist rejects it

        ok = await _rpc(
            client,
            "CreateTaskPushNotificationConfig",
            {"taskId": task_id, "url": "https://push.example.com/cb", "token": "verify-me"},
            headers=headers,
            req_id="3",
        )
        assert "error" not in ok, ok

        listed = await _rpc(
            client, "ListTaskPushNotificationConfigs", {"taskId": task_id}, headers=headers, req_id="4"
        )
        assert len(listed["result"]["configs"]) == 1

        adapter._release.set()
        for _ in range(100):
            if pushed:
                break
            await asyncio.sleep(0.05)  # up to 5s total, headroom under a busy suite
        else:
            pytest.fail("no push notification delivered")
        assert str(pushed[-1].url) == "https://push.example.com/cb"
        assert pushed[-1].headers["X-A2A-Notification-Token"] == "verify-me"


@pytest.mark.asyncio
async def test_cancel_relays_to_adapter_and_state_converges(app_and_adapter, rsa_key):
    fastapi_app, adapter, _pushed = app_and_adapter
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
        for _ in range(100):
            get_body = await _rpc(client, "GetTask", {"id": task["id"]}, headers=headers, req_id="3")
            if get_body["result"]["status"]["state"] == "TASK_STATE_CANCELED":
                break
            await asyncio.sleep(0.05)  # up to 5s total, headroom under a busy suite
        else:
            pytest.fail("task never converged to TASK_STATE_CANCELED")


@pytest.mark.asyncio
async def test_interject_relays_to_adapter_and_is_recorded(app_and_adapter, rsa_key):
    fastapi_app, adapter, _pushed = app_and_adapter
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

        # The gw_task row's write lands slightly after the client-visible
        # SendMessage response (the event reaches subscribers and gets
        # persisted via two separate paths off the same event, with no
        # ordering guarantee between them) -- and the interject endpoint
        # reads gw_task directly, not the SDK's in-memory event stream. A
        # real caller wouldn't interject in the same instant as submitting
        # either; poll like any other post-submit convergence in this file.
        for _ in range(100):
            get_body = await _rpc(client, "GetTask", {"id": task["id"]}, headers=headers, req_id="1b")
            if get_body["result"]["status"]["state"] == "TASK_STATE_WORKING":
                break
            await asyncio.sleep(0.05)  # up to 5s total, headroom under a busy suite
        else:
            pytest.fail("task never converged to TASK_STATE_WORKING in the store")

        resp = await client.post(
            f"/apps/ticket-triage/tasks/{task['id']}/interject",
            headers=headers,
            json={"text": "actually focus on Q3 only"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "accepted"
        assert body["sequence"] == 1
        assert adapter.steered == ["actually focus on Q3 only"]

        # A different principal must not be able to steer someone else's
        # task -- 404, not 403 (D1's IDOR posture, same as tasks/get).
        other_headers = _headers(_bearer_token(rsa_key, oid="ffffffff-ffff-ffff-ffff-ffffffffffff"))
        forbidden = await client.post(
            f"/apps/ticket-triage/tasks/{task['id']}/interject",
            headers=other_headers,
            json={"text": "nope"},
        )
        assert forbidden.status_code == 404

        # Release the task and confirm interjecting into a terminal task
        # is rejected -- there's nothing left to steer.
        adapter._release.set()
        for _ in range(100):
            get_body = await _rpc(client, "GetTask", {"id": task["id"]}, headers=headers, req_id="2")
            if get_body["result"]["status"]["state"] == "TASK_STATE_COMPLETED":
                break
            await asyncio.sleep(0.05)  # up to 5s total, headroom under a busy suite
        else:
            pytest.fail("task never converged to TASK_STATE_COMPLETED")

        late = await client.post(
            f"/apps/ticket-triage/tasks/{task['id']}/interject",
            headers=headers,
            json={"text": "too late"},
        )
        assert late.status_code == 409
