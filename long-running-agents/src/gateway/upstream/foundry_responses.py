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
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx

from gateway.auth.principal import Principal
from gateway.tracing import outbound_header
from gateway.upstream.base import (
    TERMINAL_STATES,
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

log = logging.getLogger(__name__)

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


# Narration derived from the polled Response's own `output` items --
# `Response.output: List[ResponseOutputItem]`, a real, standard field
# verified directly against the installed `openai` package (the actual
# runtime type of `_openai`: `AIProjectClient.get_openai_client()` is typed
# `-> AsyncOpenAI`, confirmed in `azure-ai-projects`'s own source). This
# replaces docs/05-tier2-hosted-agents.md §5.4's `ctx.emit_custom_event`/
# `gw.progress.v1` custom-event story, which turned out not to correspond
# to anything in the real, installed `agent-framework-foundry` or
# `azure-ai-agentserver-responses` packages -- see docs/08 item 16 for the
# full account. This mechanism needs no agent-side opt-in: it's derived
# from the tool-call/reasoning/message items the platform already attaches
# to every polled Response, for every T2 agent automatically.
_NARRATION: dict[str, Any] = {
    "function_call": lambda item: f"running tool: {item.name}",
    "mcp_call": lambda item: f"running tool: {item.name} (mcp: {item.server_label})",
    "code_interpreter_call": lambda item: "running code interpreter",
    "web_search_call": lambda item: "searching the web",
    "file_search_call": lambda item: "searching files",
    "azure_ai_search_call": lambda item: "searching",
    "reasoning": lambda item: "thinking",
    "message": lambda item: "drafting a response",
    "output_message": lambda item: "drafting a response",
}


def _narrate(resp: Any) -> str | None:
    """Best-effort, coarse-grained narration: which output item the model is
    currently on, not fine-grained progress within it. `resp.output` is
    ordered by the platform, so the last item is the most recent one --
    still `in_progress` if the poll landed mid-step, already `completed` if
    it landed just after. Either way it's the most useful single line to
    show. Returns `None` (never a stale guess) for item types with no
    narration mapped, or when `output` is empty (nothing has started yet)."""
    items = getattr(resp, "output", None) or []
    if not items:
        return None
    describe = _NARRATION.get(getattr(items[-1], "type", ""))
    if describe is None:
        return None
    try:
        return describe(items[-1])
    except AttributeError:
        return None


def _detail_for(resp: Any, state: TaskState) -> str | None:
    """The actual answer on completion; coarse tool-call narration before
    that. Without this split, a completed task's `StatusEvent.detail`
    stayed whatever `_narrate()` last said about the final `message` output
    item -- literally the static string "drafting a response" -- forever.
    That's narration for an answer that was still being written, attached
    to an event announcing the answer is done: not just unhelpful, actively
    wrong. `resp.output_text` is a real `openai` package convenience
    property (`Response.output_text`, verified against the installed
    package) that aggregates every `output_text` content block from
    `resp.output` -- the actual text a plain `chat.py`-style caller would
    print. Falls back to `_narrate()` if the terminal response has no text
    (a failed/canceled run, or a tool-only response with nothing to say)."""
    if state in TERMINAL_STATES:
        text = getattr(resp, "output_text", None)
        if text:
            return text
    return _narrate(resp)


def _to_text_format(output_schema: dict, *, name: str = "gw_input_required_v1") -> dict:
    """D4's per-property `required: true/false` shape (docs/02-decisions.md)
    -> real JSON Schema's object-level `required: [...]` array, wrapped in
    the Responses API's `text.format` shape -- verified against the
    installed `openai` package's `ResponseFormatTextJSONSchemaConfigParam`
    (name/schema/type required, strict optional), not guessed.

    Non-strict (`strict: False`) deliberately: OpenAI's `strict: true` mode
    requires every property in `required` (no true-optional fields --
    `question`, D4's genuinely optional field, would have to become
    nullable-but-required instead of simply absent). That's a bigger
    schema-shape change than warranted without a live Foundry endpoint to
    verify strict-mode edge cases against. The cost: no server-side
    guarantee the model's output actually conforms -- see
    `_extract_structured_status()`, which fails open rather than trusts
    that guarantee to exist.
    """
    properties: dict[str, dict] = {}
    required: list[str] = []
    for prop_name, prop in output_schema["properties"].items():
        json_prop: dict[str, Any] = {"type": prop.get("type", "string")}
        if prop.get("enum") is not None:
            json_prop["enum"] = prop["enum"]
        properties[prop_name] = json_prop
        if prop.get("required"):
            required.append(prop_name)
    return {
        "format": {
            "type": "json_schema",
            "name": name,
            "schema": {"type": "object", "properties": properties, "required": required},
            "strict": False,
        }
    }


def _extract_structured_status(resp: Any) -> tuple[TaskState, str] | None:
    """D4's fixed `status`/`message`/`question` keys -- not app-configurable,
    only the request-side schema is (`_to_text_format`). Structured output
    lands as a plain JSON *string* inside `resp.output_text` (verified
    against the installed `openai` package: no separate "structured
    output" response item type exists) -- so this is `json.loads`, not a
    lookup on some dedicated field.

    Returns `None` on anything that doesn't conform: malformed JSON, wrong
    types, a `status` value outside the D4 enum. Non-strict mode (see
    `_to_text_format`) gives no server-side guarantee the model actually
    emitted this shape, so this must degrade gracefully, never raise --
    the caller falls back to treating the response as an ordinary
    COMPLETED answer when this returns `None`.
    """
    text = getattr(resp, "output_text", None)
    if not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    message = data.get("message")
    if status not in ("answered", "needs_input") or not isinstance(message, str):
        return None
    if status == "needs_input":
        question = data.get("question")
        return TaskState.INPUT_REQUIRED, (question if isinstance(question, str) and question else message)
    return TaskState.COMPLETED, message


def new_task_id() -> str:
    return f"task_{uuid4().hex}"


async def _upload_files(openai_client: Any, files: list[InboundFile]) -> list[tuple[str, str]]:
    """Uploads inbound files via the OpenAI-compatible Files API, returns
    (file_id, mime) pairs to reference in the Responses `input` payload.

    Free function, not a method: both `FoundryResponsesAdapter` (which holds
    its own `_openai`) and `FoundryHostedAdapter` (which gets a fresh client
    per call from the project client, and never calls super().submit())
    need this, so it takes the client explicitly rather than assuming one
    lives on `self`.

    ⚠ `purpose="user_data"` is the OpenAI Files API's own "flexible file
    type for any purpose" value (docs/01 §4); not yet verified against a
    real Foundry endpoint that this is accepted and actually reaches the
    Responses API's file-input path — see docs/08.
    """
    uploaded: list[tuple[str, str]] = []
    for f in files:
        data = f.data
        if data is None:
            assert f.url is not None, "InboundFile requires data or url"
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(f.url)
                resp.raise_for_status()
                data = resp.content
        file_obj = await openai_client.files.create(file=(f.name, data, f.mime), purpose="user_data")
        uploaded.append((file_obj.id, f.mime))
    return uploaded


def _build_input(text: str, uploaded: list[tuple[str, str]]) -> str | list[dict]:
    """Plain string when there are no files -- preserves the exact request
    shape already in production use for text-only turns. Once there's at
    least one file, Responses requires the list-of-content-parts form
    (docs/01 §4): `input_image` for images (referenced by file_id, so no
    base64 ever sits in the request body), `input_file` for everything
    else."""
    if not uploaded:
        return text
    content: list[dict] = [{"type": "input_text", "text": text}]
    for file_id, mime in uploaded:
        if mime.startswith("image/"):
            content.append({"type": "input_image", "file_id": file_id, "detail": "auto"})
        else:
            content.append({"type": "input_file", "file_id": file_id})
    return [{"role": "user", "content": content}]


class FoundryResponsesAdapter:
    """Base class for Foundry Responses-API polling. Not registered as a
    standalone gateway tier — see module docstring. `FoundryHostedAdapter`
    (T2) is the only subclass actually wired into the registry.
    """

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            # COARSE, not FINE, even though follow() does narrate (via
            # _narrate()) -- it's best-effort: a poll landing before any
            # tool call has started, or an agent that never calls a tool
            # at all, gets no narration line. FINE would claim a per-step
            # guarantee this doesn't make (docs/00 design premise #4: "the
            # gateway reports what it actually has, not fabricated
            # granularity").
            progress=ProgressFidelity.COARSE,
            push=False,
            artifacts=True,  # code interpreter container files, ~1h TTL — docs/07
            # True only when this app was actually configured with a D4
            # output_schema (docs/02-decisions.md D4) -- never a fixed
            # class-wide value, since whether resume()/input-required
            # pauses work at all is genuinely per-app, not per-adapter-class.
            input_required=self._output_schema is not None,
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
        output_schema: dict | None = None,
    ):
        self._openai = openai_client
        self._agent_name = agent_name
        self._interval = poll_interval_s
        # Only needed for fetch_artifact_bytes() — a raw REST call to the
        # containers endpoint, since the injected `_openai` client has no
        # guaranteed method for it (docs/07 §5).
        self._project_endpoint = project_endpoint
        self._credential = credential
        self._output_schema = output_schema
        self._text_format = _to_text_format(output_schema) if output_schema else None

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
        trace_id: str,
    ) -> Submission:
        conv_id = ref.conversation_id
        if conv_id is None:
            conv = await self._openai.conversations.create(
                metadata={"gw_app": app}  # gw_principal stamp added by the store layer (D1)
            )
            conv_id = conv.id

        uploaded = await _upload_files(self._openai, files)
        kwargs: dict[str, Any] = dict(
            background=not blocking,
            conversation=conv_id,
            input=_build_input(text, uploaded),
            extra_body={
                "agent_reference": {"name": self._agent_name, "type": "agent_reference"}
            },
            extra_headers=self._headers(principal, trace_id),
            prompt_cache_key=principal.subject,
            safety_identifier=principal.subject,
        )
        if self._text_format is not None:
            kwargs["text"] = self._text_format
        resp = await self._openai.responses.create(**kwargs)
        return Submission(
            task_id=new_task_id(),
            context_id=conv_id,
            state=_map_state(resp.status),
            ref=UpstreamRef(conversation_id=conv_id, run_id=resp.id),
        )

    def _headers(self, principal: Principal, trace_id: str) -> dict[str, str]:
        # D1: x-ms-user-identity is documented as applying beyond hosted
        # agents. Send it here too, pending ISO-1 verification
        # (docs/02-decisions.md D1, docs/08 item A.1).
        #
        # traceparent (docs/05 §6.3, "the gap to close first"): a fresh
        # header per outbound call, same trace-id as the inbound request,
        # new span-id -- gateway.tracing.outbound_header()'s own contract.
        # Whether Foundry's container actually picks this up into its own
        # span is the part that's still unverified against a live endpoint
        # (this repo has no way to check that) -- what's verified here is
        # that the gateway sends a correctly-formed header on every single
        # call, which is the half actually in this codebase's control.
        return {
            "x-ms-user-identity": principal.user_identity_header(),
            "traceparent": outbound_header(trace_id),
        }

    async def follow(
        self,
        ref: UpstreamRef,
        *,
        task_id: str,
        principal: Principal,
        trace_id: str,
        from_sequence: int = 0,
    ) -> AsyncIterator[StatusEvent | ArtifactEvent]:
        seq = from_sequence
        while True:
            # A fresh traceparent per poll -- each poll is its own outbound
            # call/span, same trace-id throughout (docs/05 §6.3).
            resp = await self._openai.responses.retrieve(
                ref.run_id, extra_headers={"traceparent": outbound_header(trace_id)}
            )
            state = _map_state(resp.status)
            detail = _detail_for(resp, state)
            # D4 (docs/02-decisions.md): a paused-for-clarification turn
            # still reports resp.status == "completed" at the raw
            # Responses-API level -- there's no native "waiting for
            # input" status. The only way to detect the pause is the
            # response's structured *content*, checked here, never from
            # resp.status alone.
            if state == TaskState.COMPLETED and self._output_schema is not None:
                structured = _extract_structured_status(resp)
                if structured is not None:
                    state, detail = structured
                else:
                    log.warning(
                        "app configured with output_schema but completed "
                        "response did not conform to the D4 status/message "
                        "shape; showing raw output_text (run_id=%s, trace_id=%s)",
                        ref.run_id,
                        trace_id,
                    )
            # Artifacts detected in THIS poll's response are yielded before
            # its StatusEvent, not after -- a real race, not a style choice.
            # `_follow_and_relay` (executor.py) awaits each yielded event in
            # turn: harvest() + updater.add_artifact() for an ArtifactEvent,
            # updater.update_status() for a StatusEvent. a2a-sdk's own
            # EventConsumer then drains that single queue strictly FIFO and
            # persists each event before dequeuing the next (verified
            # against the installed a2a-sdk: _handle_task_modification_event
            # awaits TaskManager.process() to completion per event). So
            # whichever event we enqueue first is guaranteed persisted
            # first. Yielding the terminal StatusEvent(final=True) before
            # this poll's artifacts meant a client calling GetTask the
            # instant it observed COMPLETED could see a task with no
            # artifacts yet -- the harvest (a real network copy to blob) was
            # still in flight. samples/tier2/02-per-user-isolated-storage's
            # fake_chat_ui.py hit this directly (docs/08).
            for artifact in self._new_artifacts(resp, task_id, seq):
                seq += 1
                artifact.sequence = seq
                yield artifact
            seq += 1
            yield StatusEvent(
                task_id=task_id,
                state=state,
                sequence=seq,
                detail=detail,
                # NOT the same expression as the loop-stop check below --
                # INPUT_REQUIRED is a pause, not TERMINAL_STATES (D7), and
                # must never be reported `final=True`.
                final=state in TERMINAL_STATES,
            )
            # Deliberately a different condition than `final=` above: the
            # poll loop has nothing left to observe once paused for input
            # (the underlying Foundry response is genuinely done), so it
            # must stop here too, or it re-polls the same finished
            # response and re-yields the same question forever.
            if state in TERMINAL_STATES or state == TaskState.INPUT_REQUIRED:
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

    async def resume(
        self,
        ref: UpstreamRef,
        *,
        principal: Principal,
        text: str,
        files: list[InboundFile],
        trace_id: str,
    ) -> Submission:
        """Continues the same conversation with the caller's reply to a
        `needs_input` pause — a second `responses.create()` call against
        `ref.conversation_id`, structurally identical to `submit()`'s
        first-turn call once a conversation already exists.

        Reachable via `GatewayAgentExecutor._continue_existing()`
        (`src/gateway/a2a_server/executor.py`), which routes here whenever
        the current task's state is `TASK_STATE_INPUT_REQUIRED` -- which
        `follow()` now actually produces, for apps configured with a D4
        `output_schema` (docs/02-decisions.md D4). `text=self._text_format`
        below re-applies the same schema on the continued turn, since the
        agent may ask another clarifying question before finally answering.
        """
        if ref.conversation_id is None:
            raise ValueError("resume() requires an existing conversation_id")
        uploaded = await _upload_files(self._openai, files)
        kwargs: dict[str, Any] = dict(
            background=True,
            conversation=ref.conversation_id,
            input=_build_input(text, uploaded),
            extra_body={
                "agent_reference": {"name": self._agent_name, "type": "agent_reference"}
            },
            extra_headers=self._headers(principal, trace_id),
            prompt_cache_key=principal.subject,
            safety_identifier=principal.subject,
        )
        if self._text_format is not None:
            kwargs["text"] = self._text_format
        resp = await self._openai.responses.create(**kwargs)
        return Submission(
            task_id=new_task_id(),
            context_id=ref.conversation_id,
            state=_map_state(resp.status),
            ref=UpstreamRef(conversation_id=ref.conversation_id, run_id=resp.id),
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
