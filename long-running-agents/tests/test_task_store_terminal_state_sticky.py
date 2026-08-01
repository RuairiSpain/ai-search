"""Regression test for a real race found chasing a flaky CancelTask
convergence test (tests/test_a2a_api.py::test_cancel_relays_to_adapter_and_state_converges),
not invented speculatively.

`GatewayAgentExecutor.cancel()` (a direct write) and
`GatewayTaskStoreAdapter.save()` (the SDK's own event-consumer, which can
still be draining an already-queued status event concurrently) both derive
their `sequence` argument from a separately-fetched, potentially-stale
`task_row.last_sequence + 1` read in Python -- under concurrency they can
independently compute the *same* next sequence number. `append_event()`'s
`INSERT ... ON CONFLICT DO NOTHING` silently drops whichever one loses
that collision, but its accompanying `UPDATE gw_task SET state=...` used
to run unconditionally regardless -- so whichever writer's UPDATE
physically executed *last* won, non-deterministically. A stale WORKING
write landing after a CANCELED write reverted the state with nothing left
to ever fix it: a genuinely lost cancellation, not just a slow one.

The fix (src/gateway/store/task_store.py `append_event()`): once a task
reaches a terminal state, no further status write may move it to a
different state, regardless of write-arrival order. These tests exercise
that guard directly against real Postgres, independent of the SDK
machinery that originally surfaced it -- so this stays covered even if
`test_a2a_api.py`'s own end-to-end test doesn't happen to hit the race on
a given run.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from gateway.auth.principal import Principal
from gateway.store.context_store import ContextStore
from gateway.store.task_store import TaskStore
from gateway.upstream.base import TaskState

PRINCIPAL = Principal(subject="t2.alice", tenant="t2")


async def _new_task(pg_pool) -> tuple[ContextStore, TaskStore, str]:
    contexts = ContextStore(pg_pool)
    tasks = TaskStore(pg_pool)
    context_id = f"ctx_{uuid4().hex[:8]}"
    await contexts.get_or_create_context(context_id, "ticket-triage", PRINCIPAL)
    task_id = f"task_{uuid4().hex[:8]}"
    await tasks.create_task(
        task_id=task_id,
        context_id=context_id,
        app="ticket-triage",
        tier="t2",
        state=TaskState.SUBMITTED,
        run_id=None,
    )
    return contexts, tasks, task_id


@pytest.mark.asyncio
async def test_a_late_arriving_status_write_cannot_revert_a_terminal_state(pg_pool):
    """The exact race: a CANCELED write lands, then a WORKING write with a
    HIGHER sequence number (simulating a stale, already-queued event that
    happened to be processed after cancellation) arrives late. The task
    must stay CANCELED -- not revert to WORKING just because the reverting
    write has a bigger sequence number or arrived more recently."""
    _contexts, tasks, task_id = await _new_task(pg_pool)

    await tasks.append_event(task_id, 1, "status", {"state": "working", "final": False})
    await tasks.append_event(task_id, 2, "status", {"state": "canceled", "final": True})

    row = await tasks.get_task(task_id)
    assert row is not None
    assert row.state == "canceled"

    # The late write: higher sequence, arrives after the terminal one.
    await tasks.append_event(task_id, 3, "status", {"state": "working", "final": False})

    row = await tasks.get_task(task_id)
    assert row is not None
    assert row.state == "canceled"  # must NOT have reverted


@pytest.mark.asyncio
async def test_terminal_state_sticky_regardless_of_which_terminal_state(pg_pool):
    """Same guard, exercised for completed/failed/rejected too -- not a
    cancel()-specific special case."""
    for terminal in ("completed", "failed", "rejected"):
        _contexts, tasks, task_id = await _new_task(pg_pool)
        await tasks.append_event(task_id, 1, "status", {"state": "working", "final": False})
        await tasks.append_event(task_id, 2, "status", {"state": terminal, "final": True})
        await tasks.append_event(task_id, 3, "status", {"state": "working", "final": False})

        row = await tasks.get_task(task_id)
        assert row is not None
        assert row.state == terminal, f"expected {terminal!r} to stay sticky, got {row.state!r}"


@pytest.mark.asyncio
async def test_non_terminal_transitions_still_apply_normally(pg_pool):
    """The guard must not make the gateway deaf to ordinary progress --
    only terminal states are sticky. submitted -> working -> input-required
    -> working -> completed must all land, in order, since none of the
    intermediate states are terminal."""
    _contexts, tasks, task_id = await _new_task(pg_pool)

    await tasks.append_event(task_id, 1, "status", {"state": "working", "final": False})
    row = await tasks.get_task(task_id)
    assert row is not None
    assert row.state == "working"

    await tasks.append_event(task_id, 2, "status", {"state": "input-required", "final": False})
    row = await tasks.get_task(task_id)
    assert row is not None
    assert row.state == "input-required"

    await tasks.append_event(task_id, 3, "status", {"state": "working", "final": False})
    row = await tasks.get_task(task_id)
    assert row is not None
    assert row.state == "working"

    await tasks.append_event(task_id, 4, "status", {"state": "completed", "final": True})
    row = await tasks.get_task(task_id)
    assert row is not None
    assert row.state == "completed"
