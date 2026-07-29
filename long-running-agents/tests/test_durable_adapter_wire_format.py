"""Round-trips DurableAdapter.submit()/cancel() through a REAL a2a-sdk
server (DefaultRequestHandler + create_jsonrpc_routes, the same machinery
gateway.a2a_server.app.mount_app uses for the gateway's own surface),
standing in for a T3 upstream's own A2A server.

This is stronger verification than tests/test_durable_adapter.py's earlier
ParseDict-only check: it exercises the SDK's actual server-side dispatch,
request parsing, ActiveTask lifecycle, and response encoding -- exactly
the surface a real T3 orchestrator (built on agent-framework-a2a, which
wraps this same a2a-sdk) would run. Still not a live T3 orchestrator
(docs/08 item E.7's caveat stands), but this catches wire-format
regressions no amount of isolated ParseDict testing can.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from a2a.helpers.proto_helpers import new_task
from a2a.server.agent_execution import AgentExecutor
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

from gateway.auth.principal import Principal
from gateway.upstream.durable import DurableAdapter


class _FakeT3Executor(AgentExecutor):
    """Minimal stand-in for a real T3 orchestrator's agent-framework-a2a
    executor: submitted -> working -> completed, no real work done.

    Unlike the gateway's own GatewayAgentExecutor, this has no external
    store to pre-create the task row in, so (unlike that executor's
    deliberate choice not to) it DOES need an explicit `new_task()` enqueue
    first -- InMemoryTaskStore starts genuinely empty, and a
    TaskStatusUpdateEvent with no prior Task is rejected."""

    async def execute(self, context, event_queue) -> None:
        await event_queue.enqueue_event(
            new_task(context.task_id, context.context_id, SdkTaskState.TASK_STATE_SUBMITTED)
        )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()
        await updater.complete()

    async def cancel(self, context, event_queue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.update_status(SdkTaskState.TASK_STATE_CANCELED)


def _fake_t3_app() -> FastAPI:
    card = AgentCard(
        name="fake-t3-orchestrator",
        description="test double",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False),
    )
    handler = DefaultRequestHandler(
        agent_executor=_FakeT3Executor(), task_store=InMemoryTaskStore(), agent_card=card
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card, card_url="/.well-known/agent-card.json"),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    )
    return app


@pytest.fixture()
def durable_adapter():
    fake_app = _fake_t3_app()
    real_async_client = httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        # DurableAdapter builds its own httpx.AsyncClient(timeout=30.0) at
        # construction; route it at the in-process fake T3 server instead
        # of a real network address, keeping every other kwarg as-is.
        kwargs["transport"] = httpx.ASGITransport(app=fake_app)
        kwargs["base_url"] = "https://t3.internal"
        return real_async_client(*args, **kwargs)

    with patch("gateway.upstream.durable.httpx.AsyncClient", side_effect=_client_factory):
        adapter = DurableAdapter(
            instances=["https://t3.internal"], health_path="/healthz", event_source=None
        )
    yield adapter


@pytest.mark.asyncio
async def test_submit_round_trips_through_a_real_a2a_sdk_server(durable_adapter):
    from gateway.upstream.base import UpstreamRef

    submission = await durable_adapter.submit(
        app="deep-research",
        principal=Principal(subject="t3.alice", tenant="t3"),
        ref=UpstreamRef(),
        text="research the thing",
        files=[],
        blocking=False,
        budget_ms=0,
        trace_id="test-trace",
    )
    assert submission.task_id
    assert submission.state.value in {"completed", "working", "submitted"}


@pytest.mark.asyncio
async def test_submit_with_file_parts_round_trips(durable_adapter):
    from gateway.upstream.base import InboundFile, UpstreamRef

    submission = await durable_adapter.submit(
        app="deep-research",
        principal=Principal(subject="t3.alice", tenant="t3"),
        ref=UpstreamRef(),
        text="see attached",
        files=[InboundFile(name="brief.txt", mime="text/plain", data=b"do the research")],
        blocking=False,
        budget_ms=0,
        trace_id="test-trace",
    )
    assert submission.task_id


@pytest.mark.asyncio
async def test_cancel_round_trips_without_error(durable_adapter):
    from gateway.upstream.base import UpstreamRef

    submission = await durable_adapter.submit(
        app="deep-research",
        principal=Principal(subject="t3.alice", tenant="t3"),
        ref=UpstreamRef(),
        text="hi",
        files=[],
        blocking=False,
        budget_ms=0,
        trace_id="test-trace",
    )
    ref = UpstreamRef(run_id=submission.task_id)
    await durable_adapter.cancel(ref, principal=Principal(subject="t3.alice", tenant="t3"))
