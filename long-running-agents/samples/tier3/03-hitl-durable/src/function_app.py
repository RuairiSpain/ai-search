"""The only file the Functions platform contract touches
(docs/06-tier3-durable-agents.md §2.1). Registers the orchestrator and
activities, and bridges HTTP requests into the FastAPI A2A app from
a2a/server.py -- same `_current_client` contextvar bridge as
../../01-durable-hello-world-status/src/function_app.py, extended with one
more callback: `raise_event`, wired to `client.raise_event(instance_id,
event_name, payload)` (docs/06-tier3-durable-agents.md §5.3's own text is
the citation for this method's signature; like the rest of this bridge, it
is this sample's own proposal for combining Functions' binding-injection
model with a hand-built ASGI app, NOT independently verified against a
real deployment -- see ../../01-durable-hello-world-status/README.md
"What's NOT fully verified here", which applies here unchanged.
"""
from __future__ import annotations

import contextvars

import azure.durable_functions as df
import azure.functions as func

from a2a.server import build_app
from activities.notify import notify  # noqa: F401 -- registers via its Blueprint import
from activities.notify_timeout import notify_timeout  # noqa: F401
from activities.reimburse import reimburse  # noqa: F401
from activities.request_approval import request_approval  # noqa: F401
from orchestrations.approval import bp as orchestration_bp, expense_approval_orchestrator  # noqa: F401

app = df.DFApp(http_auth_level=func.AuthLevel.FUNCTION)
app.register_functions(orchestration_bp)

_current_client: contextvars.ContextVar[df.DurableOrchestrationClient] = contextvars.ContextVar(
    "durable_client"
)


async def _start_orchestration(task_id: str, client_input: dict) -> None:
    client = _current_client.get()
    await client.start_new("expense_approval_orchestrator", instance_id=task_id, client_input=client_input)


async def _raise_event(task_id: str, event_name: str, payload: dict) -> None:
    client = _current_client.get()
    await client.raise_event(task_id, event_name, payload)


async def _terminate(task_id: str) -> None:
    client = _current_client.get()
    await client.terminate(task_id, reason="canceled via A2A CancelTask")


a2a_app = build_app(start_orchestration=_start_orchestration, raise_event=_raise_event, terminate=_terminate)


@app.route(route="{*path}", methods=["GET", "POST"])
@app.durable_client_input(client_name="client")
async def a2a_entrypoint(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    token = _current_client.set(client)
    try:
        return await func.AsgiMiddleware(a2a_app).handle_async(req)
    finally:
        _current_client.reset(token)
