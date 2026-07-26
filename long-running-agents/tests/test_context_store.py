"""Integration tests against a real Postgres — the IDOR control (D1) and
the session-creation race fix (docs/05 §6.3) are exactly the kind of bug
that a mock store would hide. Run `make db-up && make migrate` first.
"""
from __future__ import annotations

import asyncio

import pytest

from gateway.auth.principal import Principal
from gateway.store.context_store import ContextStore
from gateway.upstream.base import UpstreamRef

ALICE = Principal(subject="t1.alice", tenant="t1")
BOB = Principal(subject="t1.bob", tenant="t1")


@pytest.mark.asyncio
async def test_context_is_not_transferable(pg_pool):
    store = ContextStore(pg_pool)
    ctx = await store.new_context("ticket-triage", ALICE)

    assert await store.authorise_context(ctx.context_id, BOB) is None
    owned = await store.authorise_context(ctx.context_id, ALICE)
    assert owned is not None
    assert owned.context_id == ctx.context_id


@pytest.mark.asyncio
async def test_unknown_context_returns_none_not_an_error(pg_pool):
    store = ContextStore(pg_pool)
    assert await store.authorise_context("ctx_does_not_exist", ALICE) is None


@pytest.mark.asyncio
async def test_many_conversations_per_user(pg_pool):
    store = ContextStore(pg_pool)
    a = await store.new_context("ticket-triage", ALICE)
    b = await store.new_context("ticket-triage", ALICE)

    assert a.context_id != b.context_id
    assert await store.authorise_context(a.context_id, ALICE) is not None
    assert await store.authorise_context(b.context_id, ALICE) is not None


@pytest.mark.asyncio
async def test_concurrent_ref_population_only_one_winner(pg_pool):
    """docs/05-tier2-hosted-agents.md §6.3 "Session creation race": two
    concurrent first turns for the same context_id must not both populate
    session_id — the loser must be told so it can terminate the orphan
    session it already created upstream."""
    store = ContextStore(pg_pool)
    ctx = await store.new_context("ticket-triage", ALICE)

    ref_a = UpstreamRef(session_id="session-a")
    ref_b = UpstreamRef(session_id="session-b")

    results = await asyncio.gather(
        store.record_upstream_ref(ctx.context_id, ALICE, ref_a),
        store.record_upstream_ref(ctx.context_id, ALICE, ref_b),
    )
    wins = [won for _, won in results]
    assert sorted(wins) == [False, True]

    final = await store.authorise_context(ctx.context_id, ALICE)
    assert final.session_id in {"session-a", "session-b"}
