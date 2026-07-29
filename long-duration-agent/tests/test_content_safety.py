import importlib.util
from unittest import mock

import pytest

from long_duration_agent.config import get_settings
from long_duration_agent.content_safety import ContentSafetyBlockedError, check_content_safety

try:
    CONTENT_SAFETY_INSTALLED = importlib.util.find_spec("azure.ai.contentsafety") is not None
except ModuleNotFoundError:
    # azure is a namespace package assembled from several separately-installed subpackages;
    # find_spec raises (rather than returning None) when an intermediate segment - here
    # "azure.ai" - doesn't exist anywhere, i.e. azure-ai-contentsafety isn't installed.
    CONTENT_SAFETY_INSTALLED = False
requires_content_safety_sdk = pytest.mark.skipif(
    not CONTENT_SAFETY_INSTALLED, reason="azure-ai-contentsafety not installed (pip install '.[content-safety]')"
)


def _configure(monkeypatch, **env):
    monkeypatch.setenv("LDA_CONTENT_SAFETY_MODE", "off")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_off_mode_never_blocks_anything(monkeypatch):
    _configure(monkeypatch, LDA_CONTENT_SAFETY_MODE="off")
    await check_content_safety("literally anything, including 'bomb' or 'kill'")


@pytest.mark.asyncio
async def test_blocklist_mode_blocks_a_matching_term_case_insensitively(monkeypatch):
    _configure(monkeypatch, LDA_CONTENT_SAFETY_MODE="blocklist", LDA_CONTENT_SAFETY_BLOCKLIST="forbidden-term")
    with pytest.raises(ContentSafetyBlockedError):
        await check_content_safety("This text contains a FORBIDDEN-TERM in the middle.")


@pytest.mark.asyncio
async def test_blocklist_mode_allows_text_with_no_match(monkeypatch):
    _configure(monkeypatch, LDA_CONTENT_SAFETY_MODE="blocklist", LDA_CONTENT_SAFETY_BLOCKLIST="forbidden-term")
    await check_content_safety("A perfectly ordinary sentence.")


@pytest.mark.asyncio
async def test_blocklist_mode_with_empty_list_blocks_nothing(monkeypatch):
    _configure(monkeypatch, LDA_CONTENT_SAFETY_MODE="blocklist", LDA_CONTENT_SAFETY_BLOCKLIST="")
    await check_content_safety("anything at all")


@pytest.mark.asyncio
async def test_unknown_mode_raises(monkeypatch):
    _configure(monkeypatch, LDA_CONTENT_SAFETY_MODE="not-a-real-mode")
    with pytest.raises(ValueError):
        await check_content_safety("hello")


@pytest.mark.asyncio
async def test_azure_mode_requires_an_endpoint(monkeypatch):
    _configure(monkeypatch, LDA_CONTENT_SAFETY_MODE="azure", AZURE_CONTENT_SAFETY_ENDPOINT="")
    with pytest.raises(RuntimeError, match="AZURE_CONTENT_SAFETY_ENDPOINT"):
        await check_content_safety("hello")


@requires_content_safety_sdk
@pytest.mark.asyncio
async def test_azure_mode_blocks_when_a_category_meets_the_severity_threshold(monkeypatch):
    _configure(
        monkeypatch,
        LDA_CONTENT_SAFETY_MODE="azure",
        AZURE_CONTENT_SAFETY_ENDPOINT="https://fake.cognitiveservices.azure.com",
        AZURE_CONTENT_SAFETY_API_KEY="fake-key",
        LDA_CONTENT_SAFETY_MAX_SEVERITY="4",
    )
    from azure.ai.contentsafety.models import AnalyzeTextResult, TextCategoriesAnalysis

    fake_result = AnalyzeTextResult(
        categories_analysis=[
            TextCategoriesAnalysis(category="Hate", severity=0),
            TextCategoriesAnalysis(category="Violence", severity=4),
        ]
    )
    with mock.patch("azure.ai.contentsafety.aio.ContentSafetyClient.analyze_text", return_value=fake_result):
        with pytest.raises(ContentSafetyBlockedError, match="Violence"):
            await check_content_safety("some violent text")


@requires_content_safety_sdk
@pytest.mark.asyncio
async def test_azure_mode_allows_text_below_the_severity_threshold(monkeypatch):
    _configure(
        monkeypatch,
        LDA_CONTENT_SAFETY_MODE="azure",
        AZURE_CONTENT_SAFETY_ENDPOINT="https://fake.cognitiveservices.azure.com",
        AZURE_CONTENT_SAFETY_API_KEY="fake-key",
        LDA_CONTENT_SAFETY_MAX_SEVERITY="4",
    )
    from azure.ai.contentsafety.models import AnalyzeTextResult, TextCategoriesAnalysis

    fake_result = AnalyzeTextResult(categories_analysis=[TextCategoriesAnalysis(category="Hate", severity=2)])
    with mock.patch("azure.ai.contentsafety.aio.ContentSafetyClient.analyze_text", return_value=fake_result):
        await check_content_safety("a mildly spicy sentence")


@pytest.mark.asyncio
async def test_blocked_prompt_surfaces_as_an_error_event_through_the_pipeline(monkeypatch):
    monkeypatch.setenv("LDA_CONTENT_SAFETY_MODE", "blocklist")
    monkeypatch.setenv("LDA_CONTENT_SAFETY_BLOCKLIST", "banned-phrase")
    get_settings.cache_clear()

    import uuid

    from long_duration_agent.durable.engine import run_translation_operation
    from long_duration_agent.models import CallerIdentity as EngineCallerIdentity
    from long_duration_agent.models import InvocationRequest

    caller = EngineCallerIdentity(tenant_id="tenant-a", user_object_id="user-1")
    request = InvocationRequest(prompt="This has a banned-phrase in it.", operation_id=str(uuid.uuid4()))

    events = [event async for event in run_translation_operation(request, caller)]

    assert events[-1].event == "error"
    assert "blocked" in events[-1].data["message"].lower()
    assert not any(e.event == "artifact" for e in events)
