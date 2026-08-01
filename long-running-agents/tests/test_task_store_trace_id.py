"""gw_task.trace_id persistence (docs/05 §6.3, docs/06 §6.3 "trace
correlation -- the gap to close first"). Against real Postgres, same
discipline as the rest of this suite's store-level tests.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from gateway.auth.principal import Principal
from gateway.store.context_store import ContextStore
from gateway.store.task_store import TaskStore
from gateway.upstream.base import TaskState

PRINCIPAL = Principal(subject="t2.alice", tenant="t2")


async def _new_context(contexts: ContextStore) -> str:
    context_id = f"ctx_{uuid4().hex[:8]}"
    await contexts.get_or_create_context(context_id, "ticket-triage", PRINCIPAL)
    return context_id


@pytest.mark.asyncio
async def test_create_task_persists_trace_id(pg_pool):
    contexts = ContextStore(pg_pool)
    tasks = TaskStore(pg_pool)
    context_id = await _new_context(contexts)
    task_id = f"task_{uuid4().hex[:8]}"

    await tasks.create_task(
        task_id=task_id,
        context_id=context_id,
        app="ticket-triage",
        tier="t2",
        state=TaskState.SUBMITTED,
        run_id=None,
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
    )

    row = await tasks.get_task(task_id)
    assert row is not None
    assert row.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"


@pytest.mark.asyncio
async def test_create_task_without_trace_id_defaults_to_none(pg_pool):
    contexts = ContextStore(pg_pool)
    tasks = TaskStore(pg_pool)
    context_id = await _new_context(contexts)
    task_id = f"task_{uuid4().hex[:8]}"

    await tasks.create_task(
        task_id=task_id,
        context_id=context_id,
        app="ticket-triage",
        tier="t2",
        state=TaskState.SUBMITTED,
        run_id=None,
    )

    row = await tasks.get_task(task_id)
    assert row is not None
    assert row.trace_id is None


@pytest.mark.asyncio
async def test_set_trace_id_overwrites_on_resume(pg_pool):
    """Mirrors set_run_id/set_current_message_id's own "reflects the
    current turn, not full history" semantics -- a resume gets its own
    fresh inbound trace, and gw_task.trace_id should track it."""
    contexts = ContextStore(pg_pool)
    tasks = TaskStore(pg_pool)
    context_id = await _new_context(contexts)
    task_id = f"task_{uuid4().hex[:8]}"

    await tasks.create_task(
        task_id=task_id,
        context_id=context_id,
        app="ticket-triage",
        tier="t2",
        state=TaskState.SUBMITTED,
        run_id=None,
        trace_id="original00000000000000000000000",
    )

    await tasks.set_trace_id(task_id, "resumed000000000000000000000000")

    row = await tasks.get_task(task_id)
    assert row is not None
    assert row.trace_id == "resumed000000000000000000000000"
