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
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol
from uuid import uuid4

import httpx

from gateway.auth.principal import Principal
from gateway.upstream.base import (
    ArtifactEvent,
    Capabilities,
    ProgressFidelity,
    StatusEvent,
    SteerResult,
    Submission,
    TaskState,
    UpstreamRef,
)


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
        self, *, app: str, principal: Principal, ref: UpstreamRef, text: str, blocking: bool, budget_ms: int
    ) -> Submission:
        message_id = uuid4().hex
        resp = await self._client.post(
            f"{self._base_url()}/",
            json={
                "jsonrpc": "2.0",
                "id": message_id,
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": message_id,
                        "role": "user",
                        "parts": [{"kind": "text", "text": text}],
                        "contextId": ref.conversation_id,
                        "metadata": {"principal_subject": principal.subject},
                    },
                    "configuration": {"blocking": blocking},
                },
            },
        )
        resp.raise_for_status()
        result = resp.json()["result"]
        task_id = result["id"]
        return Submission(
            task_id=task_id,
            context_id=result.get("contextId", ref.conversation_id or task_id),
            state=TaskState(result.get("status", {}).get("state", "submitted")),
            ref=UpstreamRef(run_id=task_id, conversation_id=result.get("contextId")),
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

    async def resume(self, ref: UpstreamRef, *, principal: Principal, text: str) -> Submission:
        # Maps onto client.raise_event(instance_id, "APPROVAL", payload) on
        # the T3 side, fronted by the same message/send path with a task
        # reference (docs/06 §5.3).
        return await self.submit(
            app="", principal=principal, ref=ref, text=text, blocking=False, budget_ms=0
        )

    async def steer(self, ref: UpstreamRef, *, principal: Principal, text: str) -> SteerResult:
        # ⚠ Verify whether the targeted A2A version permits message/send
        # against a `working` task (docs/02-decisions.md D7). Until
        # verified, treat as queued rather than claim real-time effect.
        await self.resume(ref, principal=principal, text=text)
        return SteerResult(outcome="queued", applies_at="next orchestration step")

    async def cancel(self, ref: UpstreamRef, *, principal: Principal) -> None:
        await self._client.post(
            f"{self._base_url()}/",
            json={
                "jsonrpc": "2.0",
                "id": uuid4().hex,
                "method": "tasks/cancel",
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
