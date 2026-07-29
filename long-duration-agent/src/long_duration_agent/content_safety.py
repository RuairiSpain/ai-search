"""Optional content-safety guardrail, checked on the English prompt before Translate - a
blocked prompt never reaches the model or gets written to storage.

Three modes (``LDA_CONTENT_SAFETY_MODE``):

- ``"off"`` (default) - no check at all; unchanged demo behavior.
- ``"blocklist"`` - a deterministic, offline, case-insensitive substring match against
  ``LDA_CONTENT_SAFETY_BLOCKLIST`` (comma-separated terms). No external dependency; useful for
  CI/tests and as a cheap first line of defense.
- ``"azure"`` - calls Azure AI Content Safety's ``analyze_text`` and blocks the prompt if any
  category's severity is at or above ``LDA_CONTENT_SAFETY_MAX_SEVERITY`` (default 4 - Azure's
  own "Medium" threshold on the default FourSeverityLevels output: 0/2 pass, 4/6 block).

This is checked once, on the original English prompt - not re-checked on the model's Spanish
output. The translator is instructed to translate meaning faithfully, not add new content, so
screening the input is the meaningful checkpoint; a stricter deployment could call
``check_content_safety`` a second time on the translated text using the same function.
"""

from __future__ import annotations

from .config import Settings, get_settings
from .observability import metrics


class ContentSafetyBlockedError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Content blocked by safety guardrail: {reason}")


async def check_content_safety(text: str) -> None:
    settings = get_settings()
    mode = settings.lda_content_safety_mode
    try:
        if mode == "off":
            return
        if mode == "blocklist":
            _check_blocklist(text, settings)
            return
        if mode == "azure":
            await _check_azure(text, settings)
            return
        raise ValueError(f"Unknown LDA_CONTENT_SAFETY_MODE '{mode}'.")
    except ContentSafetyBlockedError:
        metrics()["content_safety_blocked_total"].inc()
        raise


def _check_blocklist(text: str, settings: Settings) -> None:
    terms = [term.strip().lower() for term in settings.lda_content_safety_blocklist.split(",") if term.strip()]
    lowered = text.lower()
    for term in terms:
        if term in lowered:
            raise ContentSafetyBlockedError(f"matched blocked term '{term}'")


async def _check_azure(text: str, settings: Settings) -> None:
    if not settings.azure_content_safety_endpoint:
        raise RuntimeError("AZURE_CONTENT_SAFETY_ENDPOINT must be set when LDA_CONTENT_SAFETY_MODE=azure.")

    try:
        from azure.ai.contentsafety.aio import ContentSafetyClient
        from azure.ai.contentsafety.models import AnalyzeTextOptions
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without deps installed
        raise RuntimeError(
            "azure-ai-contentsafety is required for LDA_CONTENT_SAFETY_MODE=azure. Install it "
            "(pip install '.[content-safety]') or set LDA_CONTENT_SAFETY_MODE=off/blocklist."
        ) from exc

    if settings.azure_content_safety_api_key:
        from azure.core.credentials import AzureKeyCredential

        credential = AzureKeyCredential(settings.azure_content_safety_api_key)
    else:
        from azure.identity.aio import DefaultAzureCredential

        credential = DefaultAzureCredential()

    client = ContentSafetyClient(endpoint=settings.azure_content_safety_endpoint, credential=credential)
    result = await client.analyze_text(AnalyzeTextOptions(text=text))

    for category_result in result.categories_analysis:
        severity = category_result.severity or 0
        if severity >= settings.lda_content_safety_max_severity:
            raise ContentSafetyBlockedError(
                f"{category_result.category} severity {severity} >= threshold "
                f"{settings.lda_content_safety_max_severity}"
            )
