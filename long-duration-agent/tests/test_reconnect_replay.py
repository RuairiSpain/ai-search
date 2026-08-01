"""Tests for the durable, replayable per-operation event log (durable/engine.py's
_drive_and_persist / storage/metadata_store.py's append_event+list_events): a client that
reconnects to a still-running operation - or calls /respond after a HITL pause, which is
always a fresh connection since the original stream ends the moment it pauses - should see
every event already logged, replayed first with its original sequence number, before new
live events continue the same numbering rather than restarting at 1.
"""

import asyncio
import uuid

import pytest

from long_duration_agent.config import get_settings
from long_duration_agent.durable.engine import (
    respond_to_hitl,
    run_translation_operation,
    submit_steering_message,
)
from long_duration_agent.identity import CallerIdentity
from long_duration_agent.models import HitlDecisionRequest, InvocationRequest, OrchestrationStage, StreamEvent
from long_duration_agent.storage.metadata_store import get_metadata_store

CALLER = CallerIdentity(tenant_id="tenant-a", user_object_id="user-1")


async def _drain(gen):
    return [event async for event in gen]


@pytest.mark.asyncio
async def test_reconnect_replays_seeded_history_then_continues_the_sequence():
    """Deterministic check of the replay contract itself: seed the log as if a previous
    request's connection dropped after two events (the operation row is still in_progress,
    since nothing marked it completed/failed/stopped), then reconnect and verify the seeded
    events come back first, verbatim, followed by live events on a continuing sequence."""
    operation_id = str(uuid.uuid4())
    store = get_metadata_store()
    await store.start_operation(
        operation_id=operation_id, workflow_name="wf", tenant_id=CALLER.tenant_id, user_object_id=CALLER.user_object_id
    )
    seeded = [
        StreamEvent(event="status", stage=OrchestrationStage.STARTED, data={"message": "..."}, sequence=1),
        StreamEvent(event="status", stage=OrchestrationStage.TRANSLATED, data={"message": "..."}, sequence=2),
    ]
    for event in seeded:
        await store.append_event(operation_id, event)

    request = InvocationRequest(prompt="Hello", operation_id=operation_id)
    events = await _drain(run_translation_operation(request, CALLER))

    assert events[0].sequence == 1 and events[0].stage == OrchestrationStage.STARTED
    assert events[1].sequence == 2 and events[1].stage == OrchestrationStage.TRANSLATED
    assert events[2].sequence == 3  # live events continue the numbering, never restart at 1
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[-1].event == "completed"

    # The replayed events are also durably re-affirmed, not duplicated - append_event() is
    # idempotent by (operation_id, sequence), so the log ends up with exactly one row per
    # sequence number covering the whole run, seeded + live.
    full_log = await store.list_events(operation_id)
    assert [event.sequence for event in full_log] == list(range(1, len(events) + 1))


@pytest.mark.asyncio
async def test_reconnect_after_a_real_dropped_connection_replays_and_resumes(monkeypatch):
    """End-to-end: actually abandon the generator partway through a real run (as an ASGI
    server would when a client disconnects), then reconnect with the same operation_id and
    verify the first call's events are replayed verbatim before the run continues - picking
    up from its last checkpoint rather than starting over."""
    monkeypatch.setenv("LDA_WAIT_AFTER_SAVE_SECONDS", "0.3")
    get_settings.cache_clear()

    operation_id = str(uuid.uuid4())
    request = InvocationRequest(prompt="Hello, how are you?", operation_id=operation_id)

    gen = run_translation_operation(request, CALLER)
    first_call_events = []
    async for event in gen:
        first_call_events.append(event)
        if len(first_call_events) == 2:
            break
    await gen.aclose()

    store = get_metadata_store()
    operation = await store.get_operation(operation_id)
    assert operation["status"] == "in_progress"  # abandoned, not completed/failed/stopped

    reconnect_events = await _drain(run_translation_operation(request, CALLER))

    assert [(e.sequence, e.stage) for e in reconnect_events[:2]] == [
        (e.sequence, e.stage) for e in first_call_events
    ]
    assert reconnect_events[2].sequence == first_call_events[-1].sequence + 1
    assert reconnect_events[-1].event == "completed"
    assert any(e.event == "artifact" for e in reconnect_events)


@pytest.mark.asyncio
async def test_respond_to_hitl_replays_history_before_resuming(monkeypatch):
    """/respond is always a fresh connection (the original stream ends the moment it pauses
    for HITL), so it should replay everything logged so far - including the hitl_request
    itself - before continuing, the same as a POST /invocations reconnect does."""
    monkeypatch.setenv("LDA_WAIT_AFTER_SAVE_SECONDS", "0.3")
    get_settings.cache_clear()

    operation_id = str(uuid.uuid4())
    request = InvocationRequest(prompt="Original text", operation_id=operation_id)

    async def steer_soon():
        await asyncio.sleep(0.1)
        await submit_steering_message(operation_id, CALLER, "please add a postscript")

    paused_events, _ = await asyncio.gather(_drain(run_translation_operation(request, CALLER)), steer_soon())
    assert paused_events[-1].event == "hitl_request"

    resume_events = await _drain(
        respond_to_hitl(operation_id, CALLER, HitlDecisionRequest(decision="yes"))
    )

    # Everything the first stream saw - including the hitl_request - comes back first, with
    # the same sequence numbers, before the resumed run's own new events continue.
    assert [(e.sequence, e.event) for e in resume_events[: len(paused_events)]] == [
        (e.sequence, e.event) for e in paused_events
    ]
    assert resume_events[len(paused_events)].sequence == paused_events[-1].sequence + 1
    assert resume_events[-1].event == "completed"
