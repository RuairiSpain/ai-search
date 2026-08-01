"""T3's own A2A server surface -- literal copy of
../../01-durable-hello-world-status/src/a2a/server.py (structurally
identical to the real, installed-a2a-sdk test double in
tests/test_durable_adapter_wire_format.py::_fake_t3_app(), verified there
against a2a-sdk 1.1.2), renamed for this sample. This app itself has
nothing to do with push notifications -- that's entirely a GATEWAY-side
concern (`GatewayPushConfigStore`, `BasePushNotificationSender`,
`src/gateway/a2a_server/app.py`), driven by this app's card in
`apps.yaml.snippet.yaml` declaring `pushNotifications: true`, not by
anything declared on THIS server's own internal card below. This server
exists only to run the orchestration the push notifications are ABOUT.

`HelloWorldExecutor` takes `start_orchestration` as an injected callback
rather than constructing a `DurableOrchestrationClient` itself -- see
../../01-durable-hello-world-status/README.md "What's NOT fully verified
here" for why: that client is only available as a Functions binding
inside a triggered invocation, and this module needs to be importable
(for its FastAPI `app`) independent of any single invocation.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from a2a.helpers.proto_helpers import get_text_parts, new_task
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard
from a2a.types.a2a_pb2 import TaskState as SdkTaskState
from fastapi import FastAPI

StartOrchestration = Callable[[str, dict], Awaitable[None]]
TerminateOrchestration = Callable[[str], Awaitable[None]]


class HelloWorldExecutor(AgentExecutor):
    """execute() starts the orchestration and returns immediately -- it does
    NOT wait for it. All progress after this point is pushed by the
    orchestration's own `notify` activity straight to the gateway's
    webhook, out of band from this server entirely (docs/06 §4.1 diagram in
    ../../README.md). This server's own task store only matters for the
    gateway's `tasks/get` reconciliation path, not exercised by this
    sample's happy path."""

    def __init__(self, *, start_orchestration: StartOrchestration, terminate: TerminateOrchestration):
        self._start_orchestration = start_orchestration
        self._terminate = terminate

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(
            new_task(context.task_id, context.context_id, SdkTaskState.TASK_STATE_SUBMITTED)
        )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        text = "\n".join(get_text_parts(context.message.parts)) if context.message else ""
        # instance_id = task_id: makes a duplicate SendMessage retry land on
        # the same orchestration instance instead of starting a second run
        # (same idempotency reasoning as docs/06 §4.2's cron example).
        await self._start_orchestration(context.task_id, {"task_id": context.task_id, "text": text})
        await updater.start_work()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self._terminate(context.task_id)
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.update_status(SdkTaskState.TASK_STATE_CANCELED)


def build_app(*, start_orchestration: StartOrchestration, terminate: TerminateOrchestration) -> FastAPI:
    card = AgentCard(
        name="push-hello-world-t3",
        description="Durable hello-world, watched via push notifications instead of polling.",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False),  # T3 pushes, never streams -- be honest on the card
    )
    handler = DefaultRequestHandler(
        agent_executor=HelloWorldExecutor(start_orchestration=start_orchestration, terminate=terminate),
        task_store=InMemoryTaskStore(),  # projection, NOT system of record -- gw_task is (docs/06 §6.3)
        agent_card=card,
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card, card_url="/.well-known/agent-card.json"),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    )
    return app
