"""The A2A surface: `/apps/{app}/` (JSON-RPC: message/send, tasks/get,
tasks/cancel) and `/apps/{app}/tasks/{task_id}/stream` (SSE follow).

This is a working skeleton, not a certified A2A implementation — see
docs/02-decisions.md open item 5 (APIM+SSE) and docs/08 for what's still
unverified. The two rules that ARE load-bearing and fully implemented:

  * every context_id is authorised against the caller's principal before
    it is resolved to anything upstream (docs/02-decisions.md D1) — see
    `_resolve_context` below;
  * the A2A `messageId` is deduped before the upstream call (D7 "Submit
    idempotency").
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, replace
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from gateway.artifacts import ArtifactHarvester
from gateway.auth.principal import AuthError, Principal
from gateway.config import GatewayConfig
from gateway.registry import Registry
from gateway.store.artifact_store import ArtifactStore
from gateway.store.context_store import ContextRow, ContextStore
from gateway.store.task_store import TaskRow, TaskStore
from gateway.upstream.base import ArtifactEvent, StatusEvent, UpstreamRef

log = logging.getLogger(__name__)


class MessagePart(BaseModel):
    kind: str = "text"
    text: str | None = None


class Message(BaseModel):
    messageId: str
    role: str = "user"
    parts: list[MessagePart]
    contextId: str | None = None


class SendConfiguration(BaseModel):
    blocking: bool = False


class SendParams(BaseModel):
    message: Message
    configuration: SendConfiguration = Field(default_factory=SendConfiguration)


class TaskRefParams(BaseModel):
    id: str


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any]


def _text_of(message: Message) -> str:
    return "\n".join(p.text or "" for p in message.parts if p.kind == "text")


def _as_a2a_task(submission, context_id: str) -> dict[str, Any]:
    # context_id is the GATEWAY's own opaque id (ctx.context_id), never
    # `submission.context_id` — for T1/T2 that field is Foundry's own
    # conversation id. Echoing it to the client would violate D1 ("the
    # client never sees a Foundry conversation ID and never supplies
    # one") and break every subsequent turn, since authorise_context()
    # only knows the gateway's own ids.
    return {
        "id": submission.task_id,
        "contextId": context_id,
        "status": {"state": submission.state.value},
    }


def _ref_for_task(ctx: ContextRow, task: TaskRow) -> UpstreamRef:
    """UpstreamRef is split across two tables: session_id/conversation_id/
    instance_url live on gw_context (per-conversation), run_id lives on
    gw_task (per-turn). Recombine them for anything that needs to talk to
    the upstream about a specific task."""
    return replace(ctx.upstream_ref(), run_id=task.run_id)


def _event_payload(event: StatusEvent | ArtifactEvent) -> tuple[str, dict]:
    if isinstance(event, StatusEvent):
        return "status", {"state": event.state.value, "detail": event.detail, "final": event.final}
    return "artifact", {
        "artifact_id": event.artifact_id,
        "name": event.name,
        "mime": event.mime,
        "uri": event.uri,
    }


def build_router(
    config: GatewayConfig,
    registry: Registry,
    contexts: ContextStore,
    tasks: TaskStore,
    artifacts: ArtifactStore,
    harvester: ArtifactHarvester,
) -> APIRouter:
    router = APIRouter()

    def _app_config(app: str):
        try:
            return config.app(app)
        except KeyError:
            raise HTTPException(404)

    async def _principal(authorization: str | None) -> Principal:
        try:
            return registry.validator.principal_from(authorization)
        except AuthError:
            raise HTTPException(401)

    async def _resolve_context(app: str, principal: Principal, context_id: str | None) -> ContextRow:
        """THE IDOR control (D1): a client-supplied contextId is only ever
        resolved via a query that includes the principal. A miss returns
        404, not 403 — don't confirm the id exists to an unauthorised
        caller."""
        if context_id:
            ctx = await contexts.authorise_context(context_id, principal)
            if ctx is None:
                raise HTTPException(404)
            return ctx
        return await contexts.new_context(app, principal)

    @router.get("/apps/{app}/.well-known/agent-card.json")
    async def agent_card(app: str) -> JSONResponse:
        app_cfg = _app_config(app)
        adapter = registry.adapter_for_app(app)
        caps = adapter.capabilities
        return JSONResponse(
            {
                "name": app_cfg.name,
                "description": app_cfg.card.description,
                "tier": app_cfg.tier,
                "capabilities": {
                    "streaming": app_cfg.card.capabilities.streaming,
                    "pushNotifications": app_cfg.card.capabilities.pushNotifications,
                },
                "extensions": {
                    "progressFidelity": caps.progress.value,
                    "steering": caps.steering.value,
                    "artifacts": caps.artifacts,
                    "inputRequired": caps.input_required,
                },
                "preview": app_cfg.preview,
            }
        )

    @router.post("/apps/{app}/")
    async def rpc(app: str, body: JsonRpcRequest, request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        _app_config(app)
        principal = await _principal(authorization)
        adapter = registry.adapter_for_app(app)

        if body.method == "message/send":
            params = SendParams.model_validate(body.params)
            ctx = await _resolve_context(app, principal, params.message.contextId)

            fresh = await tasks.dedupe_inbound(params.message.messageId)
            if not fresh:
                # Retry of a message we've already accepted. A2A callers
                # should poll tasks/get for the outcome rather than get a
                # second submission — see D7 "Submit idempotency".
                return JSONResponse({"jsonrpc": "2.0", "id": body.id, "result": {"deduped": True}})

            app_cfg = config.app(app)
            submission = await adapter.submit(
                app=app,
                principal=principal,
                ref=ctx.upstream_ref(),
                text=_text_of(params.message),
                blocking=params.configuration.blocking or app_cfg.default_mode == "short",
                budget_ms=app_cfg.sync_budget_ms,
            )
            _ctx_row, won = await contexts.record_upstream_ref(ctx.context_id, principal, submission.ref)
            if not won:
                log.warning(
                    "session-creation race on context %s: discarding the upstream "
                    "session this request just created (docs/05 §6.3)",
                    ctx.context_id,
                )
                # Best-effort: terminate the orphaned upstream session
                # rather than leak it. Left as a TODO hook per adapter.
            await tasks.create_task(
                task_id=submission.task_id,
                context_id=ctx.context_id,
                app=app,
                tier=app_cfg.tier,
                state=submission.state,
                run_id=submission.ref.run_id,
            )
            await tasks.link_inbound_message(params.message.messageId, submission.task_id)
            return JSONResponse(
                {"jsonrpc": "2.0", "id": body.id, "result": _as_a2a_task(submission, ctx.context_id)}
            )

        if body.method == "tasks/get":
            params = TaskRefParams.model_validate(body.params)
            task_row = await tasks.get_task(params.id)
            if task_row is None:
                raise HTTPException(404)
            ctx = await contexts.authorise_context(task_row.context_id, principal)
            if ctx is None:
                raise HTTPException(404)
            # A point-in-time read of gw_task, kept current by append_event
            # (message/send poll/push) and the SSE stream below — no
            # upstream round trip needed for "what state is this in".
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": body.id,
                    "result": {
                        "id": task_row.task_id,
                        "contextId": task_row.context_id,
                        "status": {"state": task_row.state},
                    },
                }
            )

        if body.method == "tasks/cancel":
            params = TaskRefParams.model_validate(body.params)
            task_row = await tasks.get_task(params.id)
            if task_row is None:
                raise HTTPException(404)
            ctx = await contexts.authorise_context(task_row.context_id, principal)
            if ctx is None:
                raise HTTPException(404)
            await adapter.cancel(_ref_for_task(ctx, task_row), principal=principal)
            # D7: never optimistic. gw_task.state stays whatever it was
            # until the upstream actually confirms cancellation, via the
            # next poll/webhook event on the SSE stream.
            return JSONResponse(
                {"jsonrpc": "2.0", "id": body.id, "result": {"id": task_row.task_id}}
            )

        raise HTTPException(400, f"unsupported method {body.method!r}")

    @router.get("/apps/{app}/tasks/{task_id}/stream")
    async def stream(app: str, task_id: str, request: Request, from_sequence: int = 0, authorization: str | None = Header(default=None)) -> StreamingResponse:
        """SSE follow — D3: SSE everywhere the gateway itself streams.
        Resumable via `from_sequence`; treat `[DONE]` as terminal and
        reconnect with backoff on the client side.

        Every event the adapter yields is persisted to gw_event before
        it's forwarded to the client — that's what makes reconnection via
        `from_sequence` and cross-replica LISTEN/NOTIFY fan-in work (D3,
        docs/03-postgres-schema.md), not just an artifact of T1/T2's own
        poll loop. Artifact events with no `uri` yet are harvested to blob
        inline, matching "harvest during the poll loop, not at completion"
        (docs/07-artifacts-and-code-interpreter.md §2).
        """
        _app_config(app)
        principal = await _principal(authorization)
        adapter = registry.adapter_for_app(app)

        task_row = await tasks.get_task(task_id)
        if task_row is None:
            raise HTTPException(404)
        ctx = await contexts.authorise_context(task_row.context_id, principal)
        if ctx is None:
            raise HTTPException(404)
        ref = _ref_for_task(ctx, task_row)

        async def followed_and_persisted():
            fetch_bytes = getattr(adapter, "fetch_artifact_bytes", None)
            async for event in adapter.follow(
                ref, task_id=task_id, principal=principal, from_sequence=from_sequence
            ):
                if isinstance(event, ArtifactEvent) and event.uri is None and fetch_bytes is not None:
                    event = await harvester.harvest(
                        event,
                        app=app,
                        principal=principal,
                        context_id=ctx.context_id,
                        fetch_bytes=fetch_bytes,
                    )
                kind, payload = _event_payload(event)
                await tasks.append_event(task_id, event.sequence, kind, payload)
                yield event

        return StreamingResponse(sse_event_stream(followed_and_persisted()), media_type="text/event-stream")

    @router.get("/apps/{app}/artifacts/{artifact_id}")
    async def download_artifact(app: str, artifact_id: str, authorization: str | None = Header(default=None)) -> JSONResponse:
        """Mints a short-lived download URL. Never returns a raw blob URL
        or the upstream's own URI (docs/07 §2 item 4) — and only for an
        artifact whose owning task's context belongs to the caller."""
        _app_config(app)
        principal = await _principal(authorization)
        row = await artifacts.get_authorised(artifact_id, principal.subject)
        if row is None or row.state != "stored" or row.blob_key is None:
            raise HTTPException(404)
        url = await harvester.download_url(row.blob_key)
        return JSONResponse({"url": url, "name": row.name, "mime": row.mime})

    return router


async def sse_event_stream(events):
    async for event in events:
        if isinstance(event, StatusEvent):
            data = {"kind": "status", **asdict(event)}
            data["state"] = event.state.value
        else:
            # upstream_ref is internal fetch metadata (container_id/file_id
            # etc.) for the harvester's own use — never leaves the gateway.
            data = {"kind": "artifact", **asdict(event)}
            data.pop("upstream_ref", None)
        yield f"data: {json.dumps(data)}\n\n"
        if isinstance(event, StatusEvent) and event.final:
            yield "data: [DONE]\n\n"
            return
