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

Run standalone for local testing:
    uvicorn long_duration_agent.hosted_agent.app:app --port 8080
"""

from __future__ import annotations

import json
import logging

from fastapi import Depends, FastAPI
from sse_starlette.sse import EventSourceResponse

from ..durable.engine import run_translation_operation
from ..identity import CallerIdentity, resolve_caller
from ..models import InvocationRequest

logger = logging.getLogger(__name__)

app = FastAPI(title="Long-Duration Translation Agent - Hosted Agent Invocations")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/invocations")
async def invoke(request: InvocationRequest, caller: CallerIdentity = Depends(resolve_caller)) -> EventSourceResponse:
    async def event_generator():
        async for stream_event in run_translation_operation(request, caller):
            yield {
                "event": stream_event.event,
                "data": json.dumps({"stage": stream_event.stage.value, **stream_event.data}),
            }

    return EventSourceResponse(event_generator())
