"""T3's own A2A server surface for the HITL sample -- built on the same
DefaultRequestHandler + InMemoryTaskStore + routes combination as
../../01-durable-hello-world-status/src/a2a/server.py (verified there
against the installed a2a-sdk's own test double,
tests/test_durable_adapter_wire_format.py), extended with the one thing
that sample never needed: routing a SECOND message against an EXISTING
task_id into `client.raise_event(...)` instead of starting a new
orchestration -- this is what "resume" means for a durable orchestration
paused on `wait_for_external_event` (docs/06-tier3-durable-agents.md §5.3).

This mirrors, one layer further out, what the gateway's own
`GatewayAgentExecutor._continue_existing()`
(src/gateway/a2a_server/executor.py) already does: it routes an
INPUT_REQUIRED task's reply into `adapter.resume()`, and
`DurableAdapter.resume()` (src/gateway/upstream/durable.py) just re-calls
`submit()` -- another SendMessage -- against THIS server. So this server
is what actually has to know what to do with it.
"""
from __future__ import annotations

import json
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
RaiseEvent = Callable[[str, str, dict], Awaitable[None]]
TerminateOrchestration = Callable[[str], Awaitable[None]]


def _expense_request_from(context: RequestContext) -> dict:
    """A client can send either plain text (the expense description, this
    sample's default 14-day approval deadline) or a JSON object
    {"expense": ..., "timeout_seconds": ...} to override the deadline --
    same JSON-or-plain-text duality as _decision_from() below. The
    deliberate failure path this sample's README walks through (an
    approval that times out) needs some way to set a short deadline
    without a second orchestrator function to hardcode it in."""
    text = "\n".join(get_text_parts(context.message.parts)) if context.message else ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "expense" in parsed:
            return parsed
    except (ValueError, TypeError):
        pass
    return {"expense": text}


def _decision_from(context: RequestContext) -> dict:
    """This sample's own client/approve.py sends the decision as a JSON
    object in the message's text part -- {"decision": "approved"|"rejected",
    ...}. Falls back to treating unparseable text as a rejection reason
    rather than raising: a human replying with plain prose from a general
    chat client is a real case, not an error condition, and the
    orchestration's own timeout path already exists to handle "nobody gave
    a clean answer" -- this just gets there via a different route."""
    text = "\n".join(get_text_parts(context.message.parts)) if context.message else ""
    try:
        decision = json.loads(text)
        if isinstance(decision, dict) and "decision" in decision:
            return decision
    except (ValueError, TypeError):
        pass
    return {"decision": "rejected", "reason": text or "no decision text provided"}


class ApprovalExecutor(AgentExecutor):
    def __init__(
        self,
        *,
        start_orchestration: StartOrchestration,
        raise_event: RaiseEvent,
        terminate: TerminateOrchestration,
    ):
        self._start_orchestration = start_orchestration
        self._raise_event = raise_event
        self._terminate = terminate

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is not None:
            await self._continue_existing(context, event_queue)
            return

        await event_queue.enqueue_event(
            new_task(context.task_id, context.context_id, SdkTaskState.TASK_STATE_SUBMITTED)
        )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        request = _expense_request_from(context)
        client_input: dict = {"task_id": context.task_id, "text": request["expense"]}
        if "timeout_seconds" in request:
            client_input["timeout_seconds"] = request["timeout_seconds"]
        # instance_id = task_id -- same idempotency reasoning as
        # ../../01-durable-hello-world-status: a duplicate SendMessage
        # retry lands on the same orchestration instance instead of
        # starting a second one.
        await self._start_orchestration(context.task_id, client_input)
        await updater.start_work()

    async def _continue_existing(self, context: RequestContext, event_queue: EventQueue) -> None:
        """`context.current_task` comes from THIS server's own local
        InMemoryTaskStore, which -- same caveat as
        ../../01-durable-hello-world-status's a2a/server.py already
        carries for its own store -- is a reconciliation projection for
        the gateway's `tasks/get`, not the system of record (`gw_task` is,
        docs/06 §6.3). It is never told about the orchestration's own
        "now waiting for approval" state: that's pushed straight to the
        GATEWAY's webhook by the `notify` activity, out of band from this
        server entirely, same as every other status push in this sample.
        So this deliberately does NOT gate on `task.status.state` --
        in this sample's flow the only reason a second message ever
        arrives against an existing task_id is a human's decision reply,
        so unconditionally raise the APPROVAL event. (`raise_event`
        against an already-completed or already-expired instance is a
        documented no-op on the Durable Functions side, not an error this
        code has to guard against.)"""
        task = context.current_task
        assert task is not None
        decision = _decision_from(context)
        await self._raise_event(task.id, "APPROVAL", decision)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self._terminate(context.task_id)
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.update_status(SdkTaskState.TASK_STATE_CANCELED)


def build_app(
    *,
    start_orchestration: StartOrchestration,
    raise_event: RaiseEvent,
    terminate: TerminateOrchestration,
) -> FastAPI:
    card = AgentCard(
        name="expense-approval-t3",
        description="Multi-day human-in-the-loop expense approval, durable orchestration.",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False),  # T3 pushes, never streams -- be honest on the card
    )
    handler = DefaultRequestHandler(
        agent_executor=ApprovalExecutor(
            start_orchestration=start_orchestration, raise_event=raise_event, terminate=terminate
        ),
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
