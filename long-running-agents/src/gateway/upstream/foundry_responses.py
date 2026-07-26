"""Shared polling/harvest plumbing for Foundry's Responses API.

Originally the T1 (prompt agent) adapter; T1 is no longer a gateway tier
(docs/00-tier-model-and-concepts.md §4 — it gets Foundry's native incoming
A2A directly). This class survives as `FoundryHostedAdapter`'s (T2) base:
the poll loop, artifact-citation detection, and container-file fetch logic
are identical between a plain Responses call and a hosted-agent one — only
headers, the session id, and `submit()` differ, which T2 overrides.

The Foundry SDK surface is version-sensitive by design (docs/00 design
premise #3); `_openai` is typed loosely on purpose so a client swap
doesn't ripple past this file.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx

from gateway.auth.principal import Principal
from gateway.upstream.base import (
    TERMINAL_STATES,
    ArtifactEvent,
    Capabilities,
    ProgressFidelity,
    StatusEvent,
    SteerResult,
    Submission,
    TaskState,
    UpstreamRef,
)

_STATE_MAP: dict[str, TaskState] = {
    "queued": TaskState.SUBMITTED,
    "in_progress": TaskState.WORKING,
    "completed": TaskState.COMPLETED,
    "failed": TaskState.FAILED,
    "incomplete": TaskState.FAILED,
    "cancelled": TaskState.CANCELED,
}


def _map_state(status: str) -> TaskState:
    return _STATE_MAP.get(status, TaskState.WORKING)


def new_task_id() -> str:
    return f"task_{uuid4().hex}"


class FoundryResponsesAdapter:
    """Base class for Foundry Responses-API polling. Not registered as a
    standalone gateway tier — see module docstring. `FoundryHostedAdapter`
    (T2) is the only subclass actually wired into the registry.
    """

    capabilities = Capabilities(
        progress=ProgressFidelity.COARSE,
        push=False,
        artifacts=True,  # code interpreter container files, ~1h TTL — docs/07
        input_required=False,
        cancel=True,
    )

    def __init__(
        self,
        *,
        openai_client: Any,
        agent_name: str,
        poll_interval_s: float = 1.5,
        project_endpoint: str | None = None,
        credential: Any | None = None,
    ):
        self._openai = openai_client
        self._agent_name = agent_name
        self._interval = poll_interval_s
        # Only needed for fetch_artifact_bytes() — a raw REST call to the
        # containers endpoint, since the injected `_openai` client has no
        # guaranteed method for it (docs/07 §5).
        self._project_endpoint = project_endpoint
        self._credential = credential

    async def submit(
        self, *, app: str, principal: Principal, ref: UpstreamRef, text: str, blocking: bool, budget_ms: int
    ) -> Submission:
        conv_id = ref.conversation_id
        if conv_id is None:
            conv = await self._openai.conversations.create(
                metadata={"gw_app": app}  # gw_principal stamp added by the store layer (D1)
            )
            conv_id = conv.id

        resp = await self._openai.responses.create(
            background=not blocking,
            conversation=conv_id,
            input=text,
            extra_body={
                "agent_reference": {"name": self._agent_name, "type": "agent_reference"}
            },
            extra_headers=self._headers(principal),
            prompt_cache_key=principal.subject,
            safety_identifier=principal.subject,
        )
        return Submission(
            task_id=new_task_id(),
            context_id=conv_id,
            state=_map_state(resp.status),
            ref=UpstreamRef(conversation_id=conv_id, run_id=resp.id),
        )

    def _headers(self, principal: Principal) -> dict[str, str]:
        # D1: x-ms-user-identity is documented as applying beyond hosted
        # agents. Send it here too, pending ISO-1 verification
        # (docs/02-decisions.md D1, docs/08 item A.1).
        return {"x-ms-user-identity": principal.user_identity_header()}

    async def follow(
        self, ref: UpstreamRef, *, task_id: str, principal: Principal, from_sequence: int = 0
    ) -> AsyncIterator[StatusEvent | ArtifactEvent]:
        seq = from_sequence
        while True:
            resp = await self._openai.responses.retrieve(ref.run_id)
            state = _map_state(resp.status)
            seq += 1
            yield StatusEvent(
                task_id=task_id,
                state=state,
                sequence=seq,
                final=state in TERMINAL_STATES,
            )
            for artifact in self._new_artifacts(resp, task_id, seq):
                seq += 1
                artifact.sequence = seq
                yield artifact
            if state in TERMINAL_STATES:
                return
            await asyncio.sleep(self._interval)

    def _new_artifacts(self, resp: Any, task_id: str, seq: int) -> list[ArtifactEvent]:
        """Detect container_file_citation annotations as they appear.

        Deliberately called on every poll, not only at completion — a code
        interpreter container lives ~1h and a background response can
        outlive it. See docs/07-artifacts-and-code-interpreter.md §2.
        Caller (gateway.api.a2a's SSE endpoint) harvests bytes via
        fetch_artifact_bytes() and writes gw_artifact; this only detects
        new citations and carries enough upstream_ref to fetch them later.
        """
        citations = getattr(resp, "container_file_citations", None) or []
        return [
            ArtifactEvent(
                task_id=task_id,
                artifact_id=c.file_id,
                name=getattr(c, "filename", c.file_id),
                mime=getattr(c, "mime_type", "application/octet-stream"),
                sequence=seq,
                uri=None,  # filled in by the harvester after copy-to-blob
                upstream_ref={"container_id": c.container_id, "file_id": c.file_id},
            )
            for c in citations
        ]

    async def fetch_artifact_bytes(self, upstream_ref: dict) -> tuple[bytes, str]:
        """Fetch code-interpreter container file bytes directly, so the
        harvester can copy them into the gateway's own blob store before
        the container's ~1h TTL expires (docs/07 §3, §5). A raw REST call
        because the injected `_openai` client has no guaranteed method for
        the containers endpoint across SDK versions (docs/00 premise #3)."""
        if self._project_endpoint is None or self._credential is None:
            raise RuntimeError(
                "FoundryResponsesAdapter was built without project_endpoint/"
                "credential; artifact harvesting is unavailable for this upstream."
            )
        container_id = upstream_ref["container_id"]
        file_id = upstream_ref["file_id"]
        token = await self._credential.get_token("https://ai.azure.com/.default")
        url = (
            f"{self._project_endpoint.rstrip('/')}/openai/v1/containers/"
            f"{container_id}/files/{file_id}/content"
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token.token}"})
            resp.raise_for_status()
            mime = resp.headers.get("content-type", "application/octet-stream")
            return resp.content, mime

    async def resume(self, ref: UpstreamRef, *, principal: Principal, text: str) -> Submission:
        raise NotImplementedError(
            "This adapter's Capabilities.input_required is False by default; "
            "enable a conforming outputSchema (D4) before wiring resume()."
        )

    async def steer(self, ref: UpstreamRef, *, principal: Principal, text: str) -> SteerResult:
        # D7: a single Responses call offers DEFERRED steering only — the
        # running response never re-reads appended conversation items.
        await self._openai.conversations.items.create(
            conversation_id=ref.conversation_id,
            item={
                "role": "user",
                "content": f"<user_interjection>{text}</user_interjection>",
            },
        )
        return SteerResult(outcome="queued", applies_at="next turn")

    async def cancel(self, ref: UpstreamRef, *, principal: Principal) -> None:
        # ⚠ Verify endpoint shape / billing effect before trusting this in
        # production — docs/02-decisions.md D7 "Cancellation".
        await self._openai.responses.cancel(ref.run_id)

    async def artifact_url(self, ref: UpstreamRef, artifact_id: str, *, principal: Principal) -> str:
        raise NotImplementedError(
            "This base adapter's code-interpreter artifacts are harvested to "
            "blob by the poll loop; download URLs are minted from "
            "gw_artifact, not requested live from Foundry. Subclasses with a "
            "native download API (e.g. T2's Session Files) should override "
            "this. See docs/07-artifacts-and-code-interpreter.md."
        )

    async def health(self) -> bool:
        try:
            await self._openai.models.list()
            return True
        except Exception:  # noqa: BLE001 - readiness probe: any failure means unhealthy
            return False
