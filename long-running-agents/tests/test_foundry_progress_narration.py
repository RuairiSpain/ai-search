"""Tests for `_narrate()` and `_detail_for()` (src/gateway/upstream/
foundry_responses.py) -- the real, output-item-derived T2 progress
narration mechanism that replaced the `ctx.emit_custom_event`/
`gw.progress.v1` story in docs/05-tier2-hosted-agents.md §5.4, an API
confirmed (by downloading and inspecting the real `agent-framework-foundry`
and `azure-ai-agentserver-responses` packages) not to exist anywhere (see
docs/08-open-items-and-experiments.md item 16), plus `_detail_for()`'s fix
for a second gap `_narrate()` alone left behind: a completed task's
`detail` staying on the generic "drafting a response" placeholder forever
instead of ever carrying the agent's actual answer text (docs/08 item 17).

Item shapes below match the real `openai` package's `ResponseOutputItem`
union (`openai/types/responses/response_output_item.py`) field-for-field --
`SimpleNamespace` stand-ins are used purely so this test doesn't need the
`openai` package installed, not because the shape is guessed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.auth.principal import Principal
from gateway.upstream.base import TaskState, UpstreamRef
from gateway.upstream.foundry_hosted import FoundryHostedAdapter
from gateway.upstream.foundry_responses import _detail_for, _narrate

PRINCIPAL = Principal(subject="t2.alice", tenant="t2")


def _item(item_type: str, **fields) -> SimpleNamespace:
    return SimpleNamespace(type=item_type, **fields)


def _resp(
    *, status: str = "in_progress", output: list | None = None, output_text: str = ""
) -> SimpleNamespace:
    return SimpleNamespace(status=status, id="resp_1", output=output or [], output_text=output_text)


class TestNarrate:
    def test_empty_output_returns_none(self):
        assert _narrate(_resp(output=[])) is None

    def test_missing_output_attribute_returns_none(self):
        assert _narrate(SimpleNamespace(status="in_progress")) is None

    def test_function_call(self):
        resp = _resp(output=[_item("function_call", name="fetch_data", status="in_progress")])
        assert _narrate(resp) == "running tool: fetch_data"

    def test_mcp_call(self):
        resp = _resp(
            output=[_item("mcp_call", name="lookup_order", server_label="fabric-iq", status="calling")]
        )
        assert _narrate(resp) == "running tool: lookup_order (mcp: fabric-iq)"

    def test_code_interpreter_call(self):
        resp = _resp(output=[_item("code_interpreter_call", status="interpreting")])
        assert _narrate(resp) == "running code interpreter"

    def test_web_search_call(self):
        resp = _resp(output=[_item("web_search_call", status="searching")])
        assert _narrate(resp) == "searching the web"

    def test_reasoning(self):
        resp = _resp(output=[_item("reasoning", status="in_progress")])
        assert _narrate(resp) == "thinking"

    def test_message(self):
        resp = _resp(status="completed", output=[_item("message", status="completed")])
        assert _narrate(resp) == "drafting a response"

    def test_unmapped_type_returns_none_not_a_guess(self):
        resp = _resp(output=[_item("image_generation_call", status="generating")])
        assert _narrate(resp) is None

    def test_narrates_the_most_recent_item_not_the_first(self):
        resp = _resp(
            output=[
                _item("reasoning", status="completed"),
                _item("function_call", name="fetch_data", status="in_progress"),
            ]
        )
        assert _narrate(resp) == "running tool: fetch_data"

    def test_missing_expected_field_returns_none_instead_of_raising(self):
        # A function_call-typed item missing `name` shouldn't crash follow()'s
        # poll loop -- narration is best-effort, never load-bearing.
        resp = _resp(output=[_item("function_call", status="in_progress")])
        assert _narrate(resp) is None


class TestDetailFor:
    """`_detail_for()` is what follow() actually calls -- `_narrate()` alone
    would leave a completed task's detail stuck on the static "drafting a
    response" placeholder forever, since that's what `_narrate()` says
    about a `message`-type output item regardless of whether the run is
    done. This is the fix for a real gap: `StatusEvent.detail` on
    completion used to never carry the agent's actual answer."""

    def test_terminal_state_returns_output_text(self):
        resp = _resp(
            status="completed",
            output=[_item("message", status="completed")],
            output_text="Hello, world!",
        )
        assert _detail_for(resp, TaskState.COMPLETED) == "Hello, world!"

    def test_terminal_state_with_empty_output_text_falls_back_to_narrate(self):
        # A tool-only response with nothing to say -- e.g. cancel/fail with
        # no final message -- still gets whatever narration is available
        # rather than None outright.
        resp = _resp(
            status="completed",
            output=[_item("code_interpreter_call", status="completed")],
            output_text="",
        )
        assert _detail_for(resp, TaskState.COMPLETED) == "running code interpreter"

    def test_non_terminal_state_uses_narrate_even_if_output_text_present(self):
        # output_text can be non-empty mid-run too (partial text already
        # streamed into the response) -- only a TERMINAL state should
        # prefer it; otherwise a still-in-progress poll would prematurely
        # report a partial answer as if it were final.
        resp = _resp(
            status="in_progress",
            output=[_item("function_call", name="fetch_data", status="in_progress")],
            output_text="partial guess",
        )
        assert _detail_for(resp, TaskState.WORKING) == "running tool: fetch_data"

    def test_terminal_state_no_output_at_all_returns_none(self):
        resp = _resp(status="failed", output=[], output_text="")
        assert _detail_for(resp, TaskState.FAILED) is None


class _FakeResponses:
    def __init__(self, resp):
        self._resp = resp

    async def retrieve(self, run_id):
        return self._resp


class _FakeOpenAI:
    def __init__(self, resp):
        self.responses = _FakeResponses(resp)


class _FakeProjectClient:
    def __init__(self, resp):
        self._resp = resp

    def get_openai_client(self, *, agent_name: str):
        return _FakeOpenAI(self._resp)


async def _first_event(adapter, ref):
    """Takes exactly the first yielded event without exhausting the
    generator -- follow() loops (sleeping between polls) until it sees a
    terminal state, so consuming it fully would hang for a non-terminal
    fake response."""
    async for event in adapter.follow(ref, task_id="task_1", principal=PRINCIPAL, from_sequence=0):
        return event
    raise AssertionError("follow() yielded no events")


@pytest.mark.asyncio
async def test_follow_sets_detail_from_narrate():
    resp = _resp(
        status="in_progress",
        output=[_item("mcp_call", name="lookup_order", server_label="fabric-iq", status="calling")],
    )
    adapter = FoundryHostedAdapter(project_client=_FakeProjectClient(resp), agent_name="a")
    ref = UpstreamRef(conversation_id="conv_1", run_id="resp_1")

    event = await _first_event(adapter, ref)

    assert not event.final  # "in_progress" -> WORKING, not terminal
    assert event.detail == "running tool: lookup_order (mcp: fabric-iq)"


@pytest.mark.asyncio
async def test_follow_detail_none_when_nothing_narratable():
    resp = _resp(status="completed", output=[])
    adapter = FoundryHostedAdapter(project_client=_FakeProjectClient(resp), agent_name="a")
    ref = UpstreamRef(conversation_id="conv_1", run_id="resp_1")

    event = await _first_event(adapter, ref)

    assert event.final
    assert event.detail is None


@pytest.mark.asyncio
async def test_follow_delivers_the_real_answer_on_completion():
    """The gap `samples/tier2/02-per-user-isolated-storage` depends on:
    before this fix, a completed task's `detail` was stuck on `_narrate()`'s
    generic "drafting a response" -- never the actual answer text."""
    resp = _resp(
        status="completed",
        output=[_item("message", status="completed")],
        output_text="You now have 2 notes in your session.",
    )
    adapter = FoundryHostedAdapter(project_client=_FakeProjectClient(resp), agent_name="a")
    ref = UpstreamRef(conversation_id="conv_1", run_id="resp_1")

    event = await _first_event(adapter, ref)

    assert event.final
    assert event.detail == "You now have 2 notes in your session."
