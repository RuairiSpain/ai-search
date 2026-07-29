"""Translates English prompts into Spain Spanish (es-ES).

Uses a Microsoft Agent Framework chat client (Foundry project, or Azure
OpenAI/OpenAI as a fallback) when credentials are configured. Set
``LDA_USE_STUB_TRANSLATOR=1`` (the default for local/demo runs) to skip model
calls entirely and use a deterministic offline stub instead - handy for CI
and for exercising the rest of the pipeline without Azure credentials.
"""

from __future__ import annotations

from .config import get_settings

SYSTEM_PROMPT = (
    "You are a professional English-to-Spanish translator. Translate the user's text "
    "into Spain Spanish (es-ES), preserving meaning, tone, Markdown structure, code "
    "blocks, URLs, and product names exactly. Reply with only the translated text - "
    "no preamble, no explanation."
)


class TranslationError(RuntimeError):
    pass


async def translate_to_spanish(text: str) -> str:
    """Returns the es-ES translation of ``text``."""
    settings = get_settings()
    if settings.lda_use_stub_translator:
        return _stub_translate(text)
    return await _model_translate(text, settings)


def _stub_translate(text: str) -> str:
    """Deterministic, offline stand-in for the model call.

    Not a real translation - it exists so the workflow, storage, and broker
    layers can be exercised end-to-end without an Azure OpenAI/Foundry
    deployment. Replace with a real model in production by setting
    LDA_USE_STUB_TRANSLATOR=0 and configuring FOUNDRY_* or AZURE_OPENAI_*.
    """
    return f"[es-ES traducción simulada]\n{text}"


async def _model_translate(text: str, settings) -> str:
    try:
        from agent_framework.openai import OpenAIChatCompletionClient
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without deps installed
        raise TranslationError(
            "agent-framework-openai is required for live translation. Install it, or set "
            "LDA_USE_STUB_TRANSLATOR=1 to use the offline stub."
        ) from exc

    if settings.azure_openai_endpoint:
        client = OpenAIChatCompletionClient(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key or None,
            ai_model_id=settings.azure_openai_model or settings.foundry_model,
        )
    else:
        client = OpenAIChatCompletionClient(ai_model_id=settings.foundry_model)

    try:
        response = await client.get_response(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )
    except Exception as exc:  # noqa: BLE001 - surface as a domain error
        raise TranslationError(f"Translation model call failed: {exc}") from exc

    translated = getattr(response, "text", None) or str(response)
    if not translated.strip():
        raise TranslationError("Translation model returned an empty response.")
    return translated.strip()
