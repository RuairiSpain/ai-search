"""Prompt Shields (Azure AI Content Safety) over untrusted observations.

The Foundry AIServices account already exposes the Content Safety endpoint, so
no new resource is needed: POST {account}/contentsafety/text:shieldPrompt.
Defence-in-depth for §6 observation hygiene: instructions and evals defend the
injection rows in patterns 02/09/10; this adds a detector in front of the model
for code-owned loops. REST via httpx (no extra SDK); AAD token via the shared
credential. Fail-open by design for the workshop — a scanner outage shouldn't
kill runs — with the decision logged either way.
"""
from __future__ import annotations

import httpx

from .config import shared_env

_API = "2024-09-01"


def _endpoint() -> str:
    # FOUNDRY_OPENAI_ENDPOINT is the account endpoint, e.g. https://x.cognitiveservices.azure.com/
    return shared_env()["FOUNDRY_OPENAI_ENDPOINT"].rstrip("/")


def shield_observations(documents: list[str], user_prompt: str = "") -> dict:
    """Returns {"attack_detected": bool, "per_document": [...], "checked": bool}.

    checked=False means the scan itself failed (endpoint/rbac) — callers log
    and continue (fail-open), which is itself a discussion point: where would
    YOU put fail-closed?
    """
    from .foundry_client import _credential
    try:
        token = _credential().get_token("https://cognitiveservices.azure.com/.default").token
        r = httpx.post(
            f"{_endpoint()}/contentsafety/text:shieldPrompt",
            params={"api-version": _API},
            headers={"Authorization": f"Bearer {token}"},
            json={"userPrompt": user_prompt or "", "documents": documents[:20]},
            timeout=10,
        )
        r.raise_for_status()
        body = r.json()
        docs = body.get("documentsAnalysis", [])
        return {"attack_detected": any(d.get("attackDetected") for d in docs)
                or body.get("userPromptAnalysis", {}).get("attackDetected", False),
                "per_document": docs, "checked": True}
    except Exception as e:
        return {"attack_detected": False, "checked": False, "error": f"{type(e).__name__}: {e}"}


def shield_check(documents: list[str], *, user_prompt: str = "") -> dict:
    """Call shield_observations() and return a status dict every caller
    should log in full, not just branch on `attack_detected`:
    {"attack_detected": bool, "checked": bool, "reason": str | None}.

    Why this exists (item 7): shield_observations() already returns
    `checked`, but every call site in patterns 05/09/10 only read
    `attack_detected` — a misconfigured endpoint, expired token, or network
    blip made the shield silently indistinguishable from "checked and
    clean" (fail-open with no visible trace of having failed). This wrapper
    doesn't change the fail-open POLICY (still a deliberate, discussable
    choice — see SECURITY.md), it just makes it impossible to accidentally
    drop the one field that tells you whether the check actually happened.
    Callers should surface `checked=False` in their RESPONSE TEXT, not just
    an internal trace dict nobody reads (the original failure mode).
    """
    verdict = shield_observations(documents, user_prompt=user_prompt)
    checked = bool(verdict.get("checked", False))
    return {"attack_detected": bool(verdict.get("attack_detected", False)),
            "checked": checked,
            "reason": None if checked else verdict.get("error", "unknown")}
