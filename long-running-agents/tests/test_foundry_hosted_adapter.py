"""Offline test exercising the real FoundryHostedAdapter class, not a
FakeAdapter standing in for the whole UpstreamAdapter Protocol.

Every other A2A-surface test uses a FakeAdapter, which is exactly why the
`self._openai = None` bug (follow()/resume()/steer()/cancel() all
inherited from FoundryResponsesAdapter and referencing self._openai
directly, which was unconditionally None on every FoundryHostedAdapter
instance) went uncaught: nothing ever called the real class. This test
exists specifically to keep that regression from coming back.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.auth.principal import Principal
from gateway.upstream.base import UpstreamRef
from gateway.upstream.foundry_hosted import FoundryHostedAdapter

PRINCIPAL = Principal(subject="t2.alice", tenant="t2")


class _FakeResponse:
    def __init__(
        self,
        *,
        status: str = "completed",
        resp_id: str = "resp_1",
        conv_id: str = "conv_1",
        output_text: str = "",
    ):
        self.status = status
        self.id = resp_id
        self.conversation = SimpleNamespace(id=conv_id)
        self.model_extra: dict = {}
        self.container_file_citations: list = []
        self.output = []
        self.output_text = output_text


class _FakeResponses:
    async def create(self, **kwargs):
        return _FakeResponse()

    async def retrieve(self, run_id):
        return _FakeResponse()

    async def cancel(self, run_id):
        return None


class _FakeConversationItems:
    async def create(self, **kwargs):
        return None


class _FakeConversations:
    def __init__(self):
        self.items = _FakeConversationItems()

    async def create(self, **kwargs):
        return SimpleNamespace(id="conv_new")


class _FakeOpenAI:
    def __init__(self):
        self.responses = _FakeResponses()
        self.conversations = _FakeConversations()


class _FakeProjectClient:
    def __init__(self):
        self.get_client_calls = 0

    def get_openai_client(self, *, agent_name: str):
        self.get_client_calls += 1
        return _FakeOpenAI()


@pytest.mark.asyncio
async def test_inherited_methods_get_a_real_client_not_none():
    project = _FakeProjectClient()
    adapter = FoundryHostedAdapter(project_client=project, agent_name="a")
    ref = UpstreamRef(conversation_id="conv_1", run_id="resp_1")

    events = [e async for e in adapter.follow(ref, task_id="task_1", principal=PRINCIPAL)]
    assert len(events) == 1
    assert events[0].final

    # None of these may raise AttributeError on self._openai being None.
    await adapter.cancel(ref, principal=PRINCIPAL)
    await adapter.steer(ref, principal=PRINCIPAL, text="hi")
    submission = await adapter.resume(ref, principal=PRINCIPAL, text="reply", files=[])
    assert submission.ref.conversation_id == "conv_1"

    assert project.get_client_calls > 0


def test_capabilities_input_required_false_without_output_schema():
    adapter = FoundryHostedAdapter(project_client=_FakeProjectClient(), agent_name="a")
    assert adapter.capabilities.input_required is False


def test_capabilities_input_required_true_with_output_schema():
    schema = {"properties": {"status": {"type": "string", "required": True}}}
    adapter = FoundryHostedAdapter(project_client=_FakeProjectClient(), agent_name="a", output_schema=schema)
    assert adapter.capabilities.input_required is True
