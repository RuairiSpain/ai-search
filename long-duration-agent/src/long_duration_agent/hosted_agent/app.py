"""The Hosted Agent's Invocations endpoint.

``POST /invocations`` is the custom Invocations-protocol contract: the chat
UI (or a Bot Framework / Copilot Studio channel forwarding an authenticated
user) posts a prompt and gets back a stream of Server-Sent Events - status
updates as the agent works, then an artifact link, then completion.

Reconnect / long-running story: if the connection drops mid-run, POST the
same ``operation_id`` again. The durable workflow (durable/engine.py) resumes
from its last checkpoint instead of restarting, so nothing already done is
repeated - this doubles as the "durable mode" reconnect path without a
separate polling endpoint to keep in sync with the SSE stream.

Steering while the agent is running: ``POST /invocations/{operation_id}/steer``
queues additional text from the user. It's picked up at the workflow's single
steering checkpoint (always before the artifact reaches Blob Storage), which
pauses and asks for confirmation - delivered as an ``event: hitl_request`` SSE
event containing the full concatenated text. The chat UI answers via
``POST /invocations/{operation_id}/respond`` with the user's decision
(translate the combined text, edit it, or stop the operation outright).
"""

from __future__ import annotations

import json
import logging

from fastapi import Depends, FastAPI, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..durable.engine import (
    OperationAccessDeniedError,
    OperationNotFoundError,
    OperationNotSteerableError,
    check_operation_access,
    respond_to_hitl,
    run_translation_operation,
    submit_steering_message,
)
from ..identity import CallerIdentity, resolve_caller
from ..models import HitlDecisionRequest, InvocationRequest, SteerRequest

logger = logging.getLogger(__name__)

app = FastAPI(title="Long-Duration Translation Agent - Hosted Agent Invocations")


def _domain_error_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, OperationNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, OperationAccessDeniedError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, OperationNotSteerableError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _sse_events(stream_events):
    async def event_generator():
        async for stream_event in stream_events:
            yield {
                "event": stream_event.event,
                "data": json.dumps({"stage": stream_event.stage.value, **stream_event.data}),
            }

    return EventSourceResponse(event_generator())


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/invocations")
async def invoke(request: InvocationRequest, caller: CallerIdentity = Depends(resolve_caller)) -> EventSourceResponse:
    if request.operation_id:
        # Fail fast with a real HTTP status before the SSE stream opens - once streaming
        # starts the status code can no longer change. run_translation_operation()
        # re-checks the same conditions internally for callers that invoke it directly.
        try:
            operation = check_operation_access(request.operation_id, caller)
        except OperationNotFoundError:
            operation = None  # a brand-new operation_id chosen by the client - nothing to check yet
        except (OperationAccessDeniedError, OperationNotSteerableError) as exc:
            raise _domain_error_to_http(exc) from exc
        else:
            if operation["status"] == "waiting_hitl":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Operation {request.operation_id} is waiting on a HITL response; "
                        "use POST /invocations/{operation_id}/respond instead."
                    ),
                )

    return _sse_events(run_translation_operation(request, caller))


@app.post("/invocations/{operation_id}/steer")
async def steer(
    operation_id: str, request: SteerRequest, caller: CallerIdentity = Depends(resolve_caller)
) -> dict:
    """Queues a steering message. Does not itself translate or interrupt anything -
    the workflow only acts on it (with a HITL confirmation) at its next checkpoint."""
    try:
        submit_steering_message(operation_id, caller, request.text)
    except (OperationNotFoundError, OperationAccessDeniedError, OperationNotSteerableError) as exc:
        raise _domain_error_to_http(exc) from exc
    return {"accepted": True}


@app.post("/invocations/{operation_id}/respond")
async def respond(
    operation_id: str, request: HitlDecisionRequest, caller: CallerIdentity = Depends(resolve_caller)
) -> EventSourceResponse:
    """Submits the user's answer to a pending HITL request and resumes the SSE stream."""
    try:
        check_operation_access(operation_id, caller, require_status="waiting_hitl")
    except (OperationNotFoundError, OperationAccessDeniedError, OperationNotSteerableError) as exc:
        raise _domain_error_to_http(exc) from exc

    return _sse_events(respond_to_hitl(operation_id, caller, request))
