import uuid
from pathlib import Path

import pytest

from long_duration_agent.durable.engine import run_translation_operation
from long_duration_agent.identity import CallerIdentity
from long_duration_agent.models import InvocationRequest
from long_duration_agent.storage.blob_store import get_blob_store
from long_duration_agent.storage.metadata_store import get_metadata_store
from long_duration_agent.workspace import workspace_path


CALLER = CallerIdentity(tenant_id="tenant-a", user_object_id="user-1", display_name="Ada")


async def _run(operation_id: str, prompt: str = "Hello, how are you?"):
    request = InvocationRequest(prompt=prompt, operation_id=operation_id)
    events = []
    async for event in run_translation_operation(request, CALLER):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_full_pipeline_emits_expected_status_sequence_and_artifact():
    operation_id = str(uuid.uuid4())
    events = await _run(operation_id)

    kinds_and_stages = [(e.event, e.stage.value) for e in events]
    assert ("status", "started") in kinds_and_stages
    assert ("status", "translated") in kinds_and_stages
    assert ("status", "artifact_created") in kinds_and_stages
    assert ("status", "uploaded") in kinds_and_stages
    assert ("artifact", "link_ready") in kinds_and_stages
    assert ("completed", "completed") in kinds_and_stages

    # status messages match the spec's wording
    started = next(e for e in events if e.stage.value == "started")
    assert started.data["message"] == "The agent is working..."
    translated = next(e for e in events if e.stage.value == "translated")
    assert translated.data["message"] == "The text has been translated."
    created = next(e for e in events if e.stage.value == "artifact_created")
    assert created.data["message"] == "The artifact was created successfully."

    artifact_event = next(e for e in events if e.event == "artifact")
    assert artifact_event.data["download_url"].startswith("http://localhost:8081/artifacts/")
    assert "token=" in artifact_event.data["download_url"]


@pytest.mark.asyncio
async def test_artifact_is_saved_bilingually_and_local_workspace_is_cleaned_up():
    operation_id = str(uuid.uuid4())
    events = await _run(operation_id, prompt="Good morning")
    artifact_event = next(e for e in events if e.event == "artifact")
    artifact_id = artifact_event.data["artifact_id"]

    record = await get_metadata_store().get_artifact(artifact_id)
    assert record is not None
    assert record.tenant_id == "tenant-a"
    assert record.user_object_id == "user-1"
    assert record.blob_name == f"users/tenant-a/user-1/{artifact_id}.md"

    blob_store = get_blob_store()
    stream = await blob_store.open_read_stream(record.blob_name)
    try:
        content = stream.read().decode("utf-8")
    finally:
        stream.close()

    assert "# Original English Text" in content
    assert "Good morning" in content
    assert "# Traducción al Español (España)" in content
    assert "source_language: \"en\"" in content
    assert 'target_language: "es-ES"' in content

    # the hosted agent's local scratch copy must be gone once the durable copy exists
    assert not workspace_path(operation_id).exists()


@pytest.mark.asyncio
async def test_replaying_a_completed_operation_id_is_idempotent_with_a_fresh_link():
    operation_id = str(uuid.uuid4())
    first_events = await _run(operation_id)
    first_link = next(e for e in first_events if e.event == "artifact").data["download_url"]

    second_events = await _run(operation_id)
    second_link = next(e for e in second_events if e.event == "artifact").data["download_url"]

    assert first_link != second_link  # always a fresh 15-minute link, never reused

    # and it did not create a second artifact / re-run the translation
    first_artifact_id = next(e for e in first_events if e.event == "artifact").data["artifact_id"]
    second_artifact_id = next(e for e in second_events if e.event == "artifact").data["artifact_id"]
    assert first_artifact_id == second_artifact_id


@pytest.mark.asyncio
async def test_oversized_prompt_is_rejected_before_any_translation():
    operation_id = str(uuid.uuid4())
    events = await _run(operation_id, prompt="a" * 1_000_001)

    assert events[-1].event == "error"
    assert "exceeds the limit" in events[-1].data["message"]
    # no artifact should have been produced
    assert not any(e.event == "artifact" for e in events)


@pytest.mark.asyncio
async def test_replaying_after_the_artifact_has_expired_reports_an_error_not_a_stale_link():
    operation_id = str(uuid.uuid4())
    events = await _run(operation_id)
    artifact_id = next(e for e in events if e.event == "artifact").data["artifact_id"]

    store = get_metadata_store()
    await store.mark_deleted(artifact_id)  # simulates the TTL sweeper (cleanup.py) having run

    replay_events = await _run(operation_id)
    assert replay_events[-1].event == "error"
    assert not any(e.event == "artifact" for e in replay_events)
