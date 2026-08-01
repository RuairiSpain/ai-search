"""Offline test for GatewayAgentExecutor._terminate_orphaned_session() --
the session-creation-race loser cleanup (docs/05 §6.3). Previously just
logged a warning and left the orphaned upstream session to leak; now
attempts a real termination call via the adapter's optional
`terminate_session` hook when one exists.
"""
from __future__ import annotations

import logging

import pytest

from gateway.a2a_server.executor import GatewayAgentExecutor
from gateway.upstream.base import UpstreamRef


class _AdapterWithTermination:
    def __init__(self, *, fail: bool = False):
        self.terminated: list[str] = []
        self._fail = fail

    async def terminate_session(self, session_id: str) -> None:
        if self._fail:
            raise RuntimeError("upstream unreachable")
        self.terminated.append(session_id)


class _AdapterWithoutTermination:
    pass


def _executor(adapter) -> GatewayAgentExecutor:
    return GatewayAgentExecutor(
        app="ticket-triage",
        tier="t2",
        adapter=adapter,
        contexts=None,  # unused by _terminate_orphaned_session directly
        tasks=None,
        harvester=None,
        default_blocking=False,
        budget_ms=8000,
        lease_seconds=300,
    )


@pytest.mark.asyncio
async def test_terminates_orphaned_session_when_adapter_supports_it():
    adapter = _AdapterWithTermination()
    executor = _executor(adapter)
    await executor._terminate_orphaned_session("ctx_1", UpstreamRef(session_id="sess_orphan"))
    assert adapter.terminated == ["sess_orphan"]


@pytest.mark.asyncio
async def test_logs_but_does_not_raise_when_termination_fails(caplog):
    adapter = _AdapterWithTermination(fail=True)
    executor = _executor(adapter)
    with caplog.at_level(logging.ERROR):
        await executor._terminate_orphaned_session("ctx_1", UpstreamRef(session_id="sess_orphan"))
    assert "failed to terminate" in caplog.text


@pytest.mark.asyncio
async def test_no_op_when_adapter_has_no_termination_hook(caplog):
    adapter = _AdapterWithoutTermination()
    executor = _executor(adapter)
    with caplog.at_level(logging.WARNING):
        await executor._terminate_orphaned_session("ctx_1", UpstreamRef(session_id="sess_orphan"))
    assert "no terminate_session hook" in caplog.text


@pytest.mark.asyncio
async def test_no_op_when_ref_has_no_session_id():
    adapter = _AdapterWithTermination()
    executor = _executor(adapter)
    await executor._terminate_orphaned_session("ctx_1", UpstreamRef(run_id="run_only"))
    assert adapter.terminated == []
