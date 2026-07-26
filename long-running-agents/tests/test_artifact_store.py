"""Integration tests against a real Postgres for gw_artifact's
authorisation join (docs/07 §2 item 4 — a download is only ever
authorised against the principal who owns the task's conversation, never
a bare artifact_id lookup). Run `make db-up && make migrate` first.
"""
from __future__ import annotations

import pytest

from gateway.auth.principal import Principal
from gateway.store.artifact_store import ArtifactStore
from gateway.store.context_store import ContextStore
from gateway.store.task_store import TaskStore
from gateway.upstream.base import TaskState

ALICE = Principal(subject="t2.alice", tenant="t2")
BOB = Principal(subject="t2.bob", tenant="t2")


async def _make_task(pg_pool, principal: Principal) -> str:
    contexts = ContextStore(pg_pool)
    tasks = TaskStore(pg_pool)
    ctx = await contexts.new_context("ticket-triage", principal)
    task_id = f"task_{ctx.context_id}"
    await tasks.create_task(
        task_id=task_id,
        context_id=ctx.context_id,
        app="ticket-triage",
        tier="t1",
        state=TaskState.WORKING,
        run_id="resp_123",
    )
    return task_id


@pytest.mark.asyncio
async def test_artifact_download_authorised_only_for_owner(pg_pool):
    artifacts = ArtifactStore(pg_pool)
    task_id = await _make_task(pg_pool, ALICE)

    await artifacts.ensure_pending(
        artifact_id=f"{task_id}:file_1",
        task_id=task_id,
        name="chart.png",
        mime="image/png",
        upstream_ref={"container_id": "c1", "file_id": "file_1"},
    )
    await artifacts.mark_stored(
        task_id=task_id,
        artifact_id=f"{task_id}:file_1",
        blob_key="artifacts/ticket-triage/x/y/z/file_1-chart.png",
        sha256="deadbeef",
        size_bytes=1234,
    )

    owned = await artifacts.get_authorised(f"{task_id}:file_1", ALICE.subject)
    assert owned is not None
    assert owned.state == "stored"
    assert owned.blob_key == "artifacts/ticket-triage/x/y/z/file_1-chart.png"

    assert await artifacts.get_authorised(f"{task_id}:file_1", BOB.subject) is None


@pytest.mark.asyncio
async def test_ensure_pending_is_idempotent(pg_pool):
    artifacts = ArtifactStore(pg_pool)
    task_id = await _make_task(pg_pool, ALICE)
    artifact_id = f"{task_id}:file_2"

    first = await artifacts.ensure_pending(
        artifact_id=artifact_id, task_id=task_id, name="a.csv", mime="text/csv", upstream_ref=None
    )
    second = await artifacts.ensure_pending(
        artifact_id=artifact_id, task_id=task_id, name="a.csv", mime="text/csv", upstream_ref=None
    )

    assert first.artifact_id == second.artifact_id == artifact_id
    assert second.state == "pending"


@pytest.mark.asyncio
async def test_failed_harvest_is_visible(pg_pool):
    artifacts = ArtifactStore(pg_pool)
    task_id = await _make_task(pg_pool, ALICE)
    artifact_id = f"{task_id}:file_3"

    await artifacts.ensure_pending(
        artifact_id=artifact_id, task_id=task_id, name="b.csv", mime="text/csv", upstream_ref=None
    )
    await artifacts.mark_failed(task_id=task_id, artifact_id=artifact_id)

    row = await artifacts.get_authorised(artifact_id, ALICE.subject)
    assert row is not None
    assert row.state == "failed"
