import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from long_duration_agent.config import get_settings
from long_duration_agent.stale_operations import sweep_stale_operations
from long_duration_agent.storage.metadata_store import get_metadata_store
from long_duration_agent.workspace import write_workspace_file, workspace_path


async def _backdate_updated_at(operation_id: str, *, hours_ago: float) -> None:
    """Test-only: pokes the SQLite row directly since there's no public API to fake elapsed time."""
    settings = get_settings()
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn = sqlite3.connect(settings.state_db_path)
    conn.execute("UPDATE operations SET updated_at = ? WHERE operation_id = ?", (old_ts, operation_id))
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_sweep_stops_an_operation_stuck_waiting_hitl_past_the_stale_threshold(monkeypatch):
    monkeypatch.setenv("LDA_OPERATION_STALE_HOURS", "6")
    get_settings.cache_clear()

    store = get_metadata_store()
    operation_id = "stale-waiting-hitl"
    await store.start_operation(
        operation_id=operation_id, workflow_name="wf-stale", tenant_id="t1", user_object_id="u1"
    )
    await store.set_waiting_on_hitl(operation_id, request_id="req-1")
    write_workspace_file(operation_id, "leftover content")
    await _backdate_updated_at(operation_id, hours_ago=10)

    count = await sweep_stale_operations()

    assert count == 1
    operation = await store.get_operation(operation_id)
    assert operation["status"] == "stopped"
    assert not workspace_path(operation_id).exists()


@pytest.mark.asyncio
async def test_sweep_leaves_recent_operations_alone(monkeypatch):
    monkeypatch.setenv("LDA_OPERATION_STALE_HOURS", "6")
    get_settings.cache_clear()

    store = get_metadata_store()
    operation_id = "fresh-in-progress"
    await store.start_operation(
        operation_id=operation_id, workflow_name="wf-fresh", tenant_id="t1", user_object_id="u1"
    )

    count = await sweep_stale_operations()

    operation = await store.get_operation(operation_id)
    assert operation["status"] == "in_progress"
    assert count == 0


@pytest.mark.asyncio
async def test_sweep_leaves_completed_operations_alone(monkeypatch):
    monkeypatch.setenv("LDA_OPERATION_STALE_HOURS", "6")
    get_settings.cache_clear()

    store = get_metadata_store()
    operation_id = "old-but-completed"
    await store.start_operation(
        operation_id=operation_id, workflow_name="wf-done", tenant_id="t1", user_object_id="u1"
    )
    await store.complete_operation(operation_id, artifact_id=operation_id)
    await _backdate_updated_at(operation_id, hours_ago=100)

    count = await sweep_stale_operations()

    operation = await store.get_operation(operation_id)
    assert operation["status"] == "completed"  # never touched - the sweep only targets in_progress/waiting_hitl
    assert count == 0
