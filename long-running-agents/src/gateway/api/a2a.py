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
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from gateway.auth.principal import AuthError, Principal
from gateway.config import GatewayConfig
from gateway.registry import Registry
from gateway.store.context_store import ContextRow, ContextStore
from gateway.store.task_store import TaskStore
from gateway.upstream.base import StatusEvent

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


def _as_a2a_task(submission) -> dict[str, Any]:
    return {
        "id": submission.task_id,
        "contextId": submission.context_id,
        "status": {"state": submission.state.value},
    }


def build_router(config: GatewayConfig, registry: Registry, contexts: ContextStore, tasks: TaskStore) -> APIRouter:
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

            fresh = await tasks.dedupe_inbound(params.message.messageId, task_id=ctx.context_id)
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
            return JSONResponse({"jsonrpc": "2.0", "id": body.id, "result": _as_a2a_task(submission)})

        if body.method == "tasks/get":
            params = TaskRefParams.model_validate(body.params)
            # Left to the reader: join gw_task -> gw_context to re-derive
            # UpstreamRef, then call a per-tier "retrieve" path. Straight
            # SQL, no adapter method needed for a point-in-time read.
            raise HTTPException(501, "tasks/get: implement against gw_task/gw_context")

        if body.method == "tasks/cancel":
            params = TaskRefParams.model_validate(body.params)
            raise HTTPException(501, "tasks/cancel: resolve UpstreamRef then adapter.cancel()")

        raise HTTPException(400, f"unsupported method {body.method!r}")

    @router.get("/apps/{app}/tasks/{task_id}/stream")
    async def stream(app: str, task_id: str, request: Request, from_sequence: int = 0, authorization: str | None = Header(default=None)) -> StreamingResponse:
        """SSE follow — D3: SSE everywhere the gateway itself streams.
        Resumable via `from_sequence`; treat `[DONE]` as terminal and
        reconnect with backoff on the client side."""
        _app_config(app)
        await _principal(authorization)  # authenticate before anything else, even a 501

        # In a full implementation this looks up the task's UpstreamRef via
        # gw_task/gw_context scoped to the principal above, then streams
        # `sse_event_stream(registry.adapter_for_app(app).follow(ref, ...))`.
        # Sketched here as a TODO to keep the adapter contract the star of
        # this file.
        raise HTTPException(501, "resolve UpstreamRef for task_id scoped to principal, then adapter.follow()")

    return router


async def sse_event_stream(events):
    async for event in events:
        if isinstance(event, StatusEvent):
            data = {"kind": "status", **asdict(event)}
            data["state"] = event.state.value
        else:
            data = {"kind": "artifact", **asdict(event)}
        yield f"data: {json.dumps(data)}\n\n"
        if isinstance(event, StatusEvent) and event.final:
            yield "data: [DONE]\n\n"
            return
