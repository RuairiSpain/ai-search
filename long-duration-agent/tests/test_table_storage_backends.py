"""Exercises TableCheckpointStorage and TableMetadataStore against a real Azurite instance -
the multi-instance production backends (LDA_CHECKPOINT_BACKEND / LDA_METADATA_BACKEND =
azurite|azure). Skipped automatically unless both azure-data-tables is installed (the
"production" extra - not a base dependency) and Azurite is reachable, so CI environments
without either still run everything else."""

import importlib.util
import socket
import uuid

import pytest

from long_duration_agent.config import get_settings


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


try:
    AZURE_DATA_TABLES_INSTALLED = importlib.util.find_spec("azure.data.tables") is not None
except ModuleNotFoundError:
    # azure is a namespace package assembled from several separately-installed subpackages;
    # find_spec raises (rather than returning None) when an intermediate segment - here
    # "azure.data" - doesn't exist anywhere, i.e. azure-data-tables isn't installed.
    AZURE_DATA_TABLES_INSTALLED = False
AZURITE_AVAILABLE = AZURE_DATA_TABLES_INSTALLED and _port_open("127.0.0.1", 10000) and _port_open("127.0.0.1", 10002)

pytestmark = pytest.mark.skipif(
    not AZURITE_AVAILABLE,
    reason="azure-data-tables not installed (pip install '.[production]') and/or Azurite not running on 127.0.0.1:10000/10002",
)


@pytest.fixture(autouse=True)
def table_backend_env(monkeypatch):
    monkeypatch.setenv("LDA_METADATA_BACKEND", "azurite")
    monkeypatch.setenv("LDA_CHECKPOINT_BACKEND", "azurite")
    monkeypatch.setenv("LDA_STORAGE_BACKEND", "azurite")
    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setenv("LDA_OPERATIONS_TABLE_NAME", f"testops{suffix}")
    monkeypatch.setenv("LDA_ARTIFACTS_TABLE_NAME", f"testartifacts{suffix}")
    monkeypatch.setenv("LDA_STEERING_TABLE_NAME", f"teststeering{suffix}")
    monkeypatch.setenv("LDA_EVENTS_TABLE_NAME", f"testevents{suffix}")
    monkeypatch.setenv("LDA_CHECKPOINT_TABLE_NAME", f"testcheckpoints{suffix}")
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER", f"testartifacts{suffix}")
    get_settings.cache_clear()

    from long_duration_agent.durable.engine import reset_checkpoint_storage_cache
    from long_duration_agent.storage.blob_store import reset_blob_store_cache
    from long_duration_agent.storage.metadata_store import reset_metadata_store_cache

    reset_metadata_store_cache()
    reset_checkpoint_storage_cache()
    reset_blob_store_cache()
    yield
    reset_metadata_store_cache()
    reset_checkpoint_storage_cache()
    reset_blob_store_cache()


@pytest.mark.asyncio
async def test_full_pipeline_runs_against_table_storage_backends():
    from long_duration_agent.durable.engine import run_translation_operation
    from long_duration_agent.identity import CallerIdentity
    from long_duration_agent.models import InvocationRequest
    from long_duration_agent.storage.blob_store import get_blob_store
    from long_duration_agent.storage.metadata_store import get_metadata_store

    caller = CallerIdentity(tenant_id="table-tenant", user_object_id="table-user")
    operation_id = f"table-op-{uuid.uuid4().hex[:8]}"
    request = InvocationRequest(prompt="Table Storage backend test", operation_id=operation_id)

    events = [event async for event in run_translation_operation(request, caller)]

    assert events[-1].event == "completed"
    artifact_event = next(e for e in events if e.event == "artifact")

    store = get_metadata_store()
    record = await store.get_artifact(artifact_event.data["artifact_id"])
    assert record is not None
    assert record.tenant_id == "table-tenant"

    stream = await get_blob_store().open_read_stream(record.blob_name)
    try:
        content = stream.read().decode("utf-8")
    finally:
        stream.close()
    assert "Table Storage backend test" in content


@pytest.mark.asyncio
async def test_steering_hitl_loop_resumes_from_a_table_storage_checkpoint(monkeypatch):
    import asyncio

    from long_duration_agent.durable.engine import (
        respond_to_hitl,
        run_translation_operation,
        submit_steering_message,
    )
    from long_duration_agent.identity import CallerIdentity
    from long_duration_agent.models import HitlDecisionRequest, InvocationRequest
    from long_duration_agent.storage.metadata_store import get_metadata_store

    monkeypatch.setenv("LDA_WAIT_AFTER_SAVE_SECONDS", "0.3")
    get_settings.cache_clear()

    caller = CallerIdentity(tenant_id="table-tenant", user_object_id="table-user")
    operation_id = f"table-hitl-{uuid.uuid4().hex[:8]}"
    request = InvocationRequest(prompt="Table checkpoint HITL test", operation_id=operation_id)

    async def steer_soon():
        await asyncio.sleep(0.1)
        await submit_steering_message(operation_id, caller, "add a note about tables")

    events, _ = await asyncio.gather(
        _drain(run_translation_operation(request, caller)),
        steer_soon(),
    )
    assert events[-1].event == "hitl_request"

    store = get_metadata_store()
    operation = await store.get_operation(operation_id)
    assert operation["status"] == "waiting_hitl"

    resume_events = await _drain(respond_to_hitl(operation_id, caller, HitlDecisionRequest(decision="yes")))
    assert resume_events[-1].event == "completed"


@pytest.mark.asyncio
async def test_reconnect_replays_the_event_log_from_table_storage(monkeypatch):
    """The durable event log (durable/engine.py's _drive_and_persist) against the Table
    Storage backend: abandon a real run partway through, reconnect with the same
    operation_id, and confirm the first call's events are replayed verbatim before the
    run continues on a sequence that doesn't restart at 1."""
    from long_duration_agent.durable.engine import run_translation_operation
    from long_duration_agent.identity import CallerIdentity
    from long_duration_agent.models import InvocationRequest
    from long_duration_agent.storage.metadata_store import get_metadata_store

    monkeypatch.setenv("LDA_WAIT_AFTER_SAVE_SECONDS", "0.3")
    get_settings.cache_clear()

    caller = CallerIdentity(tenant_id="table-tenant", user_object_id="table-user")
    operation_id = f"table-reconnect-{uuid.uuid4().hex[:8]}"
    request = InvocationRequest(prompt="Table Storage reconnect test", operation_id=operation_id)

    gen = run_translation_operation(request, caller)
    first_call_events = []
    async for event in gen:
        first_call_events.append(event)
        if len(first_call_events) == 2:
            break
    await gen.aclose()

    store = get_metadata_store()
    operation = await store.get_operation(operation_id)
    assert operation["status"] == "in_progress"

    reconnect_events = await _drain(run_translation_operation(request, caller))

    assert [e.sequence for e in reconnect_events[:2]] == [e.sequence for e in first_call_events]
    assert reconnect_events[2].sequence == first_call_events[-1].sequence + 1
    assert reconnect_events[-1].event == "completed"


async def _drain(gen):
    return [event async for event in gen]
