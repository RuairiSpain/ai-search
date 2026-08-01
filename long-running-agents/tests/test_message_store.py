"""Integration tests against a real Postgres for gw_message
(docs/08-open-items-and-experiments.md item 17). Run `make db-up && make
migrate` first.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from a2a.types.a2a_pb2 import Message, Part, Role

from gateway.auth.principal import Principal
from gateway.store.context_store import ContextStore
from gateway.store.message_store import MessageStore
from gateway.store.task_store import TaskStore
from gateway.upstream.base import TaskState

ALICE = Principal(subject="t2.alice", tenant="t2")


async def _make_task(pg_pool, principal: Principal) -> str:
    contexts = ContextStore(pg_pool)
    tasks = TaskStore(pg_pool)
    ctx = await contexts.new_context("ticket-triage", principal)
    task_id = f"task_{ctx.context_id}"
    await tasks.create_task(
        task_id=task_id,
        context_id=ctx.context_id,
        app="ticket-triage",
        tier="t2",
        state=TaskState.WORKING,
        run_id="resp_123",
    )
    return task_id


def _text_message(role, text: str) -> Message:
    # message_id is a real global primary key here, exactly like production
    # (a2a-sdk mints these via uuid4() too) -- a literal "m-1" reused across
    # test functions sharing one persistent, non-torn-down local Postgres
    # (same reasoning as FakeAdapter.submit()'s task_id in test_a2a_api.py)
    # would silently collide with an earlier test's row and vanish under
    # ON CONFLICT (message_id) DO NOTHING. Caught by actually running this
    # against real Postgres, not assumed safe.
    return Message(message_id=f"m-{uuid4().hex[:8]}", role=role, parts=[Part(text=text)])


@pytest.mark.asyncio
async def test_append_messages_is_idempotent_on_message_id(pg_pool):
    messages = MessageStore(pg_pool)
    task_id = await _make_task(pg_pool, ALICE)
    m1 = _text_message(Role.ROLE_USER, "hi")
    m2 = _text_message(Role.ROLE_AGENT, "hello")

    # save() hands append_messages() the full, growing history on every
    # call, not a delta -- this is the exact shape it re-sends.
    await messages.append_messages(task_id, [m1])
    await messages.append_messages(task_id, [m1, m2])

    result = await messages.list_for_task(task_id)
    assert [m.message_id for m in result] == [m1.message_id, m2.message_id]


@pytest.mark.asyncio
async def test_list_for_task_preserves_original_order_across_repeated_saves(pg_pool):
    messages = MessageStore(pg_pool)
    task_id = await _make_task(pg_pool, ALICE)
    m1 = _text_message(Role.ROLE_USER, "one")
    m2 = _text_message(Role.ROLE_AGENT, "two")
    m3 = _text_message(Role.ROLE_USER, "three")

    # Three "save() calls" across a conversation, each re-sending
    # everything seen so far plus one new tail message.
    await messages.append_messages(task_id, [m1])
    await messages.append_messages(task_id, [m1, m2])
    await messages.append_messages(task_id, [m1, m2, m3])

    result = await messages.list_for_task(task_id)
    assert [m.message_id for m in result] == [m1.message_id, m2.message_id, m3.message_id]


@pytest.mark.asyncio
async def test_role_and_content_round_trip(pg_pool):
    messages = MessageStore(pg_pool)
    task_id = await _make_task(pg_pool, ALICE)
    original = Message(
        message_id=f"m-{uuid4().hex[:8]}",
        role=Role.ROLE_AGENT,
        parts=[
            Part(text="here's your file"),
            Part(raw=b"\x00\x01binarydata\xff", filename="x.bin", media_type="application/octet-stream"),
        ],
    )

    await messages.append_messages(task_id, [original])
    [result] = await messages.list_for_task(task_id)

    assert result == original
    assert result.parts[1].raw == b"\x00\x01binarydata\xff"


@pytest.mark.asyncio
async def test_list_for_task_scoped_to_task_id(pg_pool):
    messages = MessageStore(pg_pool)
    task_a = await _make_task(pg_pool, ALICE)
    task_b = await _make_task(pg_pool, ALICE)
    m_a = _text_message(Role.ROLE_USER, "for a")
    m_b = _text_message(Role.ROLE_USER, "for b")

    await messages.append_messages(task_a, [m_a])
    await messages.append_messages(task_b, [m_b])

    result_a = await messages.list_for_task(task_a)
    result_b = await messages.list_for_task(task_b)

    assert [m.message_id for m in result_a] == [m_a.message_id]
    assert [m.message_id for m in result_b] == [m_b.message_id]
