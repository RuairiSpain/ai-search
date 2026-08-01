"""Exercises translator._model_translate - the real (non-stub) translation path - against the
actual agent_framework SDK classes (FoundryChatClient / OpenAIChatCompletionClient), with only
their network-calling get_response() mocked out. This is a distinct concern from
test_workflow.py's coverage: those tests all set LDA_USE_STUB_TRANSLATOR=1 and never construct
a real chat client at all.

agent_framework.foundry / agent_framework.openai are lazy shim modules that always import
successfully but raise ModuleNotFoundError on first attribute access if the real
agent-framework-foundry/agent-framework-openai distribution isn't installed - so the "SDK
missing" tests below need no skip (they exercise exactly that lazy-shim failure, which is the
real behavior in this repo's base [dev] install), while the "SDK present" tests are skipped
cleanly if the optional `translate` extra isn't installed.
"""

import importlib.util
from unittest import mock

import pytest

from long_duration_agent.config import get_settings
from long_duration_agent.translator import TranslationError, translate_to_spanish

AGENT_FRAMEWORK_OPENAI_INSTALLED = importlib.util.find_spec("agent_framework_openai") is not None
AGENT_FRAMEWORK_FOUNDRY_INSTALLED = importlib.util.find_spec("agent_framework_foundry") is not None

requires_openai_sdk = pytest.mark.skipif(
    not AGENT_FRAMEWORK_OPENAI_INSTALLED, reason="agent-framework-openai not installed (pip install '.[translate]')"
)
requires_foundry_sdk = pytest.mark.skipif(
    not AGENT_FRAMEWORK_FOUNDRY_INSTALLED, reason="agent-framework-foundry not installed (pip install '.[translate]')"
)


def _configure(monkeypatch, **env):
    monkeypatch.setenv("LDA_USE_STUB_TRANSLATOR", "0")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


class _FakeChatResponse:
    def __init__(self, text: str) -> None:
        self.text = text


@pytest.mark.asyncio
async def test_foundry_sdk_missing_raises_a_clear_translation_error(monkeypatch):
    """Without agent-framework-foundry installed, agent_framework.foundry's lazy shim raises
    ModuleNotFoundError on attribute access - verifies translator.py surfaces that as a
    TranslationError pointing at the extra to install, not a raw ModuleNotFoundError."""
    if AGENT_FRAMEWORK_FOUNDRY_INSTALLED:
        pytest.skip("agent-framework-foundry is installed; this test targets its absence")
    _configure(monkeypatch, FOUNDRY_PROJECT_ENDPOINT="https://fake.services.ai.azure.com/api/projects/demo")
    with pytest.raises(TranslationError, match="agent-framework-foundry"):
        await translate_to_spanish("Hello there")


@pytest.mark.asyncio
async def test_openai_sdk_missing_raises_a_clear_translation_error(monkeypatch):
    if AGENT_FRAMEWORK_OPENAI_INSTALLED:
        pytest.skip("agent-framework-openai is installed; this test targets its absence")
    _configure(monkeypatch, FOUNDRY_PROJECT_ENDPOINT="")
    with pytest.raises(TranslationError, match="agent-framework-openai"):
        await translate_to_spanish("Hello there")


@requires_foundry_sdk
@pytest.mark.asyncio
async def test_foundry_client_is_constructed_with_the_configured_project_and_model(monkeypatch):
    _configure(
        monkeypatch,
        FOUNDRY_PROJECT_ENDPOINT="https://fake.services.ai.azure.com/api/projects/demo",
        FOUNDRY_MODEL="gpt-4o-mini",
    )
    from agent_framework.foundry import FoundryChatClient

    with (
        mock.patch.object(FoundryChatClient, "__init__", return_value=None) as mock_init,
        mock.patch.object(
            FoundryChatClient,
            "get_response",
            new_callable=mock.AsyncMock,
            return_value=_FakeChatResponse("Hola, ¿cómo estás?"),
        ),
        mock.patch("azure.identity.aio.DefaultAzureCredential"),
    ):
        result = await translate_to_spanish("Hello, how are you?")

    assert result == "Hola, ¿cómo estás?"
    _args, kwargs = mock_init.call_args
    assert kwargs["project_endpoint"] == "https://fake.services.ai.azure.com/api/projects/demo"
    assert kwargs["model"] == "gpt-4o-mini"


@requires_foundry_sdk
@pytest.mark.asyncio
async def test_foundry_translation_call_failure_becomes_a_translation_error(monkeypatch):
    _configure(monkeypatch, FOUNDRY_PROJECT_ENDPOINT="https://fake.services.ai.azure.com/api/projects/demo")
    from agent_framework.foundry import FoundryChatClient

    with (
        mock.patch.object(FoundryChatClient, "__init__", return_value=None),
        mock.patch.object(
            FoundryChatClient, "get_response", new_callable=mock.AsyncMock, side_effect=RuntimeError("boom")
        ),
        mock.patch("azure.identity.aio.DefaultAzureCredential"),
    ):
        with pytest.raises(TranslationError, match="boom"):
            await translate_to_spanish("Hello")


@requires_foundry_sdk
@pytest.mark.asyncio
async def test_foundry_empty_response_is_rejected(monkeypatch):
    _configure(monkeypatch, FOUNDRY_PROJECT_ENDPOINT="https://fake.services.ai.azure.com/api/projects/demo")
    from agent_framework.foundry import FoundryChatClient

    with (
        mock.patch.object(FoundryChatClient, "__init__", return_value=None),
        mock.patch.object(
            FoundryChatClient, "get_response", new_callable=mock.AsyncMock, return_value=_FakeChatResponse("   ")
        ),
        mock.patch("azure.identity.aio.DefaultAzureCredential"),
    ):
        with pytest.raises(TranslationError, match="empty"):
            await translate_to_spanish("Hello")


@requires_openai_sdk
@pytest.mark.asyncio
async def test_azure_openai_client_is_constructed_with_endpoint_key_and_model(monkeypatch):
    _configure(
        monkeypatch,
        FOUNDRY_PROJECT_ENDPOINT="",
        AZURE_OPENAI_ENDPOINT="https://fake.openai.azure.com",
        AZURE_OPENAI_API_KEY="fake-key",
        AZURE_OPENAI_MODEL="gpt-4o",
    )
    from agent_framework.openai import OpenAIChatCompletionClient

    with (
        mock.patch.object(OpenAIChatCompletionClient, "__init__", return_value=None) as mock_init,
        mock.patch.object(
            OpenAIChatCompletionClient,
            "get_response",
            new_callable=mock.AsyncMock,
            return_value=_FakeChatResponse("Hola mundo"),
        ),
    ):
        result = await translate_to_spanish("Hello world")

    assert result == "Hola mundo"
    _args, kwargs = mock_init.call_args
    assert kwargs["azure_endpoint"] == "https://fake.openai.azure.com"
    assert kwargs["api_key"] == "fake-key"
    assert kwargs["model"] == "gpt-4o"


@requires_openai_sdk
@pytest.mark.asyncio
async def test_plain_openai_client_is_used_when_no_azure_endpoint_is_configured(monkeypatch):
    _configure(monkeypatch, FOUNDRY_PROJECT_ENDPOINT="", AZURE_OPENAI_ENDPOINT="", FOUNDRY_MODEL="gpt-4o-mini")
    from agent_framework.openai import OpenAIChatCompletionClient

    with (
        mock.patch.object(OpenAIChatCompletionClient, "__init__", return_value=None) as mock_init,
        mock.patch.object(
            OpenAIChatCompletionClient,
            "get_response",
            new_callable=mock.AsyncMock,
            return_value=_FakeChatResponse("Hola"),
        ),
    ):
        result = await translate_to_spanish("Hi")

    assert result == "Hola"
    _args, kwargs = mock_init.call_args
    assert kwargs.get("azure_endpoint") is None
    assert kwargs["model"] == "gpt-4o-mini"


@requires_openai_sdk
@pytest.mark.asyncio
async def test_the_system_prompt_and_user_text_are_sent_as_message_objects(monkeypatch):
    """translate_to_spanish must send agent_framework.Message objects (not raw dicts) - a
    regression test for a bug already fixed once (see docs/architecture.md history)."""
    _configure(monkeypatch, FOUNDRY_PROJECT_ENDPOINT="")
    from agent_framework import Message
    from agent_framework.openai import OpenAIChatCompletionClient

    captured = {}

    async def _fake_get_response(self, *, messages, **kwargs):
        captured["messages"] = messages
        return _FakeChatResponse("Hola")

    with (
        mock.patch.object(OpenAIChatCompletionClient, "__init__", return_value=None),
        mock.patch.object(OpenAIChatCompletionClient, "get_response", _fake_get_response),
    ):
        await translate_to_spanish("Hi there")

    messages = captured["messages"]
    assert len(messages) == 2
    assert all(isinstance(m, Message) for m in messages)
    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert messages[1].text == "Hi there"
