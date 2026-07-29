import uuid

import pytest

from long_duration_agent.durable.engine import (
    OperationAccessDeniedError,
    OperationNotSteerableError,
    respond_to_hitl,
    run_translation_operation,
    submit_steering_message,
)
from long_duration_agent.identity import CallerIdentity
from long_duration_agent.models import HitlDecisionRequest, InvocationRequest
from long_duration_agent.storage.blob_store import get_blob_store
from long_duration_agent.storage.metadata_store import get_metadata_store
from long_duration_agent.workspace import workspace_path

CALLER = CallerIdentity(tenant_id="tenant-a", user_object_id="user-1")
OTHER_CALLER = CallerIdentity(tenant_id="tenant-b", user_object_id="user-2")


async def _drain(gen):
    return [event async for event in gen]


async def _run_to_hitl(operation_id: str, prompt: str, steer_text: str, monkeypatch):
    """Starts an operation and injects a steering message while it's still working.

    Needs a non-zero pause between the markdown save and the steering checkpoint so
    there's a real window to queue the message mid-flight - the default test fixture
    sets both pipeline waits to 0 for speed, so this overrides just the first one.
    """
    import asyncio

    from long_duration_agent.config import get_settings

    monkeypatch.setenv("LDA_WAIT_AFTER_SAVE_SECONDS", "0.3")
    get_settings.cache_clear()

    request = InvocationRequest(prompt=prompt, operation_id=operation_id)

    async def steer_soon():
        await asyncio.sleep(0.1)
        submit_steering_message(operation_id, CALLER, steer_text)

    events, _ = await asyncio.gather(_drain(run_translation_operation(request, CALLER)), steer_soon())
    return events


@pytest.mark.asyncio
async def test_steering_message_pauses_for_hitl_with_concatenated_text(monkeypatch):
    operation_id = str(uuid.uuid4())
    events = await _run_to_hitl(operation_id, "Original text", "please add a postscript", monkeypatch)

    assert events[-1].event == "hitl_request"
    assert events[-1].data["full_text"] == "Original text\n\nplease add a postscript"

    operation = get_metadata_store().get_operation(operation_id)
    assert operation["status"] == "waiting_hitl"
    assert operation["pending_request_id"]


@pytest.mark.asyncio
async def test_hitl_yes_translates_concatenated_text_and_completes(monkeypatch):
    operation_id = str(uuid.uuid4())
    await _run_to_hitl(operation_id, "Original text", "please add a postscript", monkeypatch)

    events = await _drain(respond_to_hitl(operation_id, CALLER, HitlDecisionRequest(decision="yes")))

    assert events[-1].event == "completed"
    artifact_event = next(e for e in events if e.event == "artifact")

    record = get_metadata_store().get_artifact(artifact_event.data["artifact_id"])
    stream = await get_blob_store().open_read_stream(record.blob_name)
    try:
        content = stream.read().decode("utf-8")
    finally:
        stream.close()
    assert "Original text" in content
    assert "please add a postscript" in content

    operation = get_metadata_store().get_operation(operation_id)
    assert operation["status"] == "completed"


@pytest.mark.asyncio
async def test_hitl_edit_replaces_the_prompt_entirely(monkeypatch):
    operation_id = str(uuid.uuid4())
    await _run_to_hitl(operation_id, "Original text", "this suggestion gets discarded", monkeypatch)

    events = await _drain(
        respond_to_hitl(
            operation_id, CALLER, HitlDecisionRequest(decision="edit", edited_text="A brand new edited prompt")
        )
    )

    artifact_event = next(e for e in events if e.event == "artifact")
    record = get_metadata_store().get_artifact(artifact_event.data["artifact_id"])
    stream = await get_blob_store().open_read_stream(record.blob_name)
    try:
        content = stream.read().decode("utf-8")
    finally:
        stream.close()
    assert "A brand new edited prompt" in content
    assert "Original text" not in content
    assert "this suggestion gets discarded" not in content


@pytest.mark.asyncio
async def test_hitl_stop_cancels_and_cleans_up_local_file(monkeypatch):
    operation_id = str(uuid.uuid4())
    await _run_to_hitl(operation_id, "Original text", "irrelevant", monkeypatch)

    events = await _drain(respond_to_hitl(operation_id, CALLER, HitlDecisionRequest(decision="stop")))

    assert any(e.event == "stopped" for e in events)
    assert not any(e.event == "artifact" for e in events)

    operation = get_metadata_store().get_operation(operation_id)
    assert operation["status"] == "stopped"
    assert operation["artifact_id"] is None
    assert not workspace_path(operation_id).exists()
    assert get_metadata_store().get_artifact(operation_id) is None


@pytest.mark.asyncio
async def test_steer_rejected_for_operation_owned_by_someone_else(monkeypatch):
    operation_id = str(uuid.uuid4())
    await _run_to_hitl(operation_id, "Original text", "steering text", monkeypatch)

    with pytest.raises(OperationAccessDeniedError):
        submit_steering_message(operation_id, OTHER_CALLER, "sneaky message")


@pytest.mark.asyncio
async def test_respond_rejected_for_operation_owned_by_someone_else(monkeypatch):
    operation_id = str(uuid.uuid4())
    await _run_to_hitl(operation_id, "Original text", "steering text", monkeypatch)

    with pytest.raises(OperationAccessDeniedError):
        async for _ in respond_to_hitl(operation_id, OTHER_CALLER, HitlDecisionRequest(decision="yes")):
            pass


@pytest.mark.asyncio
async def test_steer_rejected_once_operation_has_completed():
    operation_id = str(uuid.uuid4())
    request = InvocationRequest(prompt="No steering here", operation_id=operation_id)
    await _drain(run_translation_operation(request, CALLER))

    with pytest.raises(OperationNotSteerableError):
        submit_steering_message(operation_id, CALLER, "too late")


@pytest.mark.asyncio
async def test_respond_rejected_when_not_waiting_on_hitl():
    operation_id = str(uuid.uuid4())
    request = InvocationRequest(prompt="No steering here either", operation_id=operation_id)
    await _drain(run_translation_operation(request, CALLER))

    with pytest.raises(OperationNotSteerableError):
        async for _ in respond_to_hitl(operation_id, CALLER, HitlDecisionRequest(decision="yes")):
            pass


@pytest.mark.asyncio
async def test_no_steering_message_takes_the_unaffected_fast_path():
    operation_id = str(uuid.uuid4())
    request = InvocationRequest(prompt="Nothing to steer", operation_id=operation_id)
    events = await _drain(run_translation_operation(request, CALLER))

    assert not any(e.event == "hitl_request" for e in events)
    assert events[-1].event == "completed"
