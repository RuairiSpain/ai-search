"""T3 adapter — MAF + Durable Task, fronted by an A2A-to-A2A upstream.

docs/01-gateway-config-and-adapter-contract.md §2 "T3 — MAF + Durable
Task", docs/06-tier3-durable-agents.md §4.1.

Unlike T2, this adapter does not poll: `submit()`/`resume()`/`cancel()`
call the T3 A2A server over plain HTTP, and `follow()` reads events that a
webhook receiver (gateway/api/webhooks.py) already wrote to gw_event —
because the T3 A2A server does not stream, it pushes. `event_source` is
the thin read side of that table so this module never imports the store
layer's SQL directly (docs/00 design premise #3: adapters stay free of
storage concerns beyond the UpstreamRef they're handed).

The JSON-RPC calls here talk to the T3 upstream's *own* A2A server, built
with `agent-framework-a2a` on the same `a2a-sdk` the gateway's own surface
uses (docs/01 §4) — so the wire shape must match what that SDK's dispatcher
actually parses: PascalCase method names (`SendMessage`, not
`message/send`), flat `Part` fields with no `kind` discriminator, and
`SendMessageConfiguration.returnImmediately` rather than a `blocking` flag.
An earlier version of this file predated verifying that against the real
package (Phase 3) and never got corrected here — found and fixed while
wiring in file-part support (docs/08 item E, Phase 4), not by inspection.
"""
from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import uuid4

import httpx
from a2a.utils import constants as a2a_constants

from gateway.auth.principal import Principal
from gateway.upstream.base import (
    ArtifactEvent,
    Capabilities,
    InboundFile,
    ProgressFidelity,
    StatusEvent,
    SteerResult,
    Submission,
    TaskState,
    UpstreamRef,
)

# Every outbound call to the T3 upstream's own A2A server must carry this
# -- its own `validate_version` decorator (verified against the installed
# a2a-sdk) defaults a request with no version header to protocol 0.3 and
# rejects it outright. Missing entirely until a real a2a-sdk-backed test
# double (not just ParseDict) surfaced it as VERSION_NOT_SUPPORTED on
# every single call (docs/08 item E.7) -- the same bug class as the one
# already fixed on the gateway's own inbound surface in
# a2a_server/context.py, just on the outbound side this time.
_A2A_HEADERS = {a2a_constants.VERSION_HEADER: a2a_constants.PROTOCOL_VERSION_1_0}

# a2a-sdk's wire-format task state strings -> our TaskState vocabulary.
# The task_id/context_id fields already happen to match our own naming
# (camelCase JSON of the same concepts), but the state enum does not --
# it's the SDK's own `TASK_STATE_*` names, not our lowercase ones.
_SDK_STATE_TO_GW: dict[str, TaskState] = {
    "TASK_STATE_SUBMITTED": TaskState.SUBMITTED,
    "TASK_STATE_WORKING": TaskState.WORKING,
    "TASK_STATE_INPUT_REQUIRED": TaskState.INPUT_REQUIRED,
    "TASK_STATE_COMPLETED": TaskState.COMPLETED,
    "TASK_STATE_FAILED": TaskState.FAILED,
    "TASK_STATE_CANCELED": TaskState.CANCELED,
    "TASK_STATE_REJECTED": TaskState.REJECTED,
    "TASK_STATE_AUTH_REQUIRED": TaskState.AUTH_REQUIRED,
}


def _parts_for(text: str, files: list[InboundFile]) -> list[dict[str, Any]]:
    """Builds the A2A `Part` list for an outbound message: the text part,
    plus one part per file, relayed as-is rather than uploaded anywhere --
    unlike T2, there's no Foundry Files API in this path, just another A2A
    endpoint that can carry the same `raw`/`url` part shape onward to
    whatever the T3 orchestrator's own agent does with it. `raw` is a
    protobuf `bytes` field, so its JSON form is base64 (verified by
    round-tripping through the installed a2a-sdk's ParseDict)."""
    parts: list[dict[str, Any]] = [{"text": text}]
    for f in files:
        part: dict[str, Any] = {"filename": f.name, "mediaType": f.mime}
        if f.data is not None:
            part["raw"] = base64.b64encode(f.data).decode()
        else:
            part["url"] = f.url
        parts.append(part)
    return parts


class EventSource(Protocol):
    """Read side of gw_event, satisfied by gateway.store.task_store."""

    async def events_after(
        self, task_id: str, from_sequence: int
    ) -> list[StatusEvent | ArtifactEvent]: ...

    async def wait_for_new_event(self, task_id: str, timeout_s: float) -> bool:
        """True if a new event landed within timeout_s; False on timeout.
        Backed by LISTEN/NOTIFY on gw_event — docs/03-postgres-schema.md.
        """
        ...


class DurableAdapter:
    capabilities = Capabilities(
        progress=ProgressFidelity.FINE,
        push=True,
        artifacts=True,
        input_required=True,
        cancel=True,
        # T3 steering: wait_for_external_event raced via task_any — checkpoint
        # granularity is "next orchestration step" (docs/02-decisions.md D7).
    )

    def __init__(self, *, instances: list[str], health_path: str, event_source: EventSource):
        if not instances:
            raise ValueError("DurableAdapter requires at least one instance URL")
        self._instances = instances
        self._health_path = health_path
        self._events = event_source
        self._client = httpx.AsyncClient(timeout=30.0)

    def _base_url(self) -> str:
        # v1: no gateway->instance affinity for T3-on-DTS (any worker can
        # resume any orchestration — docs/06 §6.1 "affinity was wrong").
        # A single upstream is round-robin-safe; if `instances` grows,
        # route by simple rotation here rather than pinning per task.
        return self._instances[0]

    async def submit(
        self,
        *,
        app: str,
        principal: Principal,
        ref: UpstreamRef,
        text: str,
        files: list[InboundFile],
        blocking: bool,
        budget_ms: int,
    ) -> Submission:
        message_id = uuid4().hex
        resp = await self._client.post(
            f"{self._base_url()}/",
            headers=_A2A_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": message_id,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": message_id,
                        "role": "ROLE_USER",
                        "parts": _parts_for(text, files),
                        "contextId": ref.conversation_id,
                        "metadata": {"principal_subject": principal.subject},
                    },
                    # Never block the gateway's own HTTP call on T3's
                    # potentially multi-day orchestration -- the gateway
                    # always submits non-blocking regardless of the `blocking`
                    # flag's own value (default_blocking=False, docs/00 §4);
                    # returnImmediately=True is what makes on_message_send on
                    # the T3 side return a submitted/working snapshot instead
                    # of waiting for a terminal state.
                    "configuration": {"returnImmediately": True},
                },
            },
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"T3 SendMessage failed: {body['error']}")
        task = body["result"].get("task")
        if task is None:
            raise RuntimeError(
                f"T3 SendMessage returned a message instead of a task: {body['result']}"
            )
        task_id = task["id"]
        sdk_state = task.get("status", {}).get("state", "TASK_STATE_SUBMITTED")
        return Submission(
            task_id=task_id,
            context_id=task.get("contextId", ref.conversation_id or task_id),
            state=_SDK_STATE_TO_GW.get(sdk_state, TaskState.WORKING),
            ref=UpstreamRef(run_id=task_id, conversation_id=task.get("contextId")),
        )

    async def follow(
        self, ref: UpstreamRef, *, task_id: str, principal: Principal, from_sequence: int = 0
    ) -> AsyncIterator[StatusEvent | ArtifactEvent]:
        """Relay events the webhook receiver already persisted under
        `task_id` (the gateway's own id — the T3 orchestrator posts to
        `/callback/tasks/{task_id}/events` using the id we handed back
        from submit(), so this is already the right key; `ref` is unused
        here but kept for Protocol symmetry with T2). No polling of the
        upstream — T3 pushes (docs/06 §4.1)."""
        seq = from_sequence
        while True:
            batch = await self._events.events_after(task_id, seq)
            for event in batch:
                seq = event.sequence
                yield event
                if isinstance(event, StatusEvent) and event.final:
                    return
            if not batch:
                await self._events.wait_for_new_event(task_id, timeout_s=30.0)

    async def fetch_artifact_bytes(self, upstream_ref: dict) -> tuple[bytes, str]:
        """Harvests a T3 artifact into the shared blob container, same as
        T2's containers-endpoint fetch — `_follow_and_relay()` calls this
        whenever a relayed `ArtifactEvent.uri` is still `None`
        (gateway.a2a_server.executor), via `getattr(self._adapter,
        "fetch_artifact_bytes", None)`. Before this existed, that getattr
        always returned `None` for `DurableAdapter`, so a T3 artifact
        pushed with `upstream_ref` but no pre-set `uri` was silently
        dropped -- never added to the task, never an error either
        (docs/08).

        Unlike T2, the gateway has no REST convention of its own for
        fetching a T3 artifact's bytes -- T3 runs arbitrary orchestrator
        code with its own interim storage, so the orchestrator must push
        a fetchable URL itself. ⚠ Verify this contract (a `download_url`
        key in `upstream_ref`, plain unauthenticated GET) against a real
        T3 orchestrator; nothing in this codebase runs one yet — see
        docs/08 item E on the wire-format fix found in the same file.
        """
        url = upstream_ref.get("download_url")
        if not url:
            raise RuntimeError(
                "T3 ArtifactEvent.upstream_ref missing 'download_url' -- the "
                "orchestrator must push a fetchable URL for the gateway to "
                "harvest, or copy the file to the shared blob container "
                "itself and set ArtifactEvent.uri directly instead."
            )
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            mime = resp.headers.get("content-type", "application/octet-stream")
            return resp.content, mime

    async def resume(
        self, ref: UpstreamRef, *, principal: Principal, text: str, files: list[InboundFile]
    ) -> Submission:
        # Maps onto client.raise_event(instance_id, "APPROVAL", payload) on
        # the T3 side, fronted by the same message/send path with a task
        # reference (docs/06 §5.3).
        return await self.submit(
            app="", principal=principal, ref=ref, text=text, files=files, blocking=False, budget_ms=0
        )

    async def steer(self, ref: UpstreamRef, *, principal: Principal, text: str) -> SteerResult:
        # ⚠ Verify whether the targeted A2A version permits message/send
        # against a `working` task (docs/02-decisions.md D7). Until
        # verified, treat as queued rather than claim real-time effect.
        await self.resume(ref, principal=principal, text=text, files=[])
        return SteerResult(outcome="queued", applies_at="next orchestration step")

    async def cancel(self, ref: UpstreamRef, *, principal: Principal) -> None:
        await self._client.post(
            f"{self._base_url()}/",
            headers=_A2A_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": uuid4().hex,
                "method": "CancelTask",
                "params": {"id": ref.run_id},
            },
        )

    async def artifact_url(self, ref: UpstreamRef, artifact_id: str, *, principal: Principal) -> str:
        raise NotImplementedError(
            "T3 artifacts are harvested to the shared blob container by "
            "activities/artifacts.py on the orchestrator side; download "
            "URLs are minted from gw_artifact, same as every other tier."
        )

    async def health(self) -> bool:
        try:
            resp = await self._client.get(f"{self._base_url()}{self._health_path}")
            return resp.status_code == 200
        except Exception:  # noqa: BLE001 - readiness probe: any failure means unhealthy
            return False
