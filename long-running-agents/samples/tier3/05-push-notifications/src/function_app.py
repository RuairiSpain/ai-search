"""The only file the Functions platform contract touches
(docs/06-tier3-durable-agents.md §2.1). Registers the orchestrator and
activity, and bridges HTTP requests into the FastAPI A2A app from
a2a/server.py. Literal copy of
../../01-durable-hello-world-status/src/function_app.py's bridge -- this
sample's orchestration is a shortened copy of that one's, see
orchestrations/hello_world.py.

⚠ The bridge below (`_current_client` contextvar) is this sample's own
answer to the open seam called out in
../../01-durable-hello-world-status/README.md "What's NOT fully verified
here": a Durable Functions client is only available as a binding
inside a Functions HTTP-trigger invocation, but `a2a.server.build_app()`
needs a plain async callable it can call from any request. Setting a
contextvar in the trigger function immediately before handing the request
to `AsgiMiddleware` and reading it from the callbacks is a standard-enough
Python pattern for bridging request-scoped state into an ASGI app, but this
specific combination with the Durable Functions binding model is NOT
verified against a real deployment -- treat it as this sample's proposal,
not a documented Microsoft pattern.
"""
from __future__ import annotations

import contextvars

import azure.durable_functions as df
import azure.functions as func

from a2a.server import build_app
from activities.notify import notify  # noqa: F401 -- registers via its Blueprint import
from orchestrations.hello_world import bp as orchestration_bp, hello_world_orchestrator  # noqa: F401

app = df.DFApp(http_auth_level=func.AuthLevel.FUNCTION)
app.register_functions(orchestration_bp)

_current_client: contextvars.ContextVar[df.DurableOrchestrationClient] = contextvars.ContextVar(
    "durable_client"
)


async def _start_orchestration(task_id: str, client_input: dict) -> None:
    client = _current_client.get()
    await client.start_new("hello_world_orchestrator", instance_id=task_id, client_input=client_input)


async def _terminate(task_id: str) -> None:
    client = _current_client.get()
    await client.terminate(task_id, reason="canceled via A2A CancelTask")


a2a_app = build_app(start_orchestration=_start_orchestration, terminate=_terminate)


@app.route(route="{*path}", methods=["GET", "POST"])
@app.durable_client_input(client_name="client")
async def a2a_entrypoint(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    token = _current_client.set(client)
    try:
        return await func.AsgiMiddleware(a2a_app).handle_async(req)
    finally:
        _current_client.reset(token)
