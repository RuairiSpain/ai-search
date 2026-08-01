"""Deterministic rules engine tools (pattern 04 neuro-symbolic).
The rules live in code, versioned, testable — NOT in a prompt (§14)."""
from __future__ import annotations

RULES = [
    {"id": "KYC-001", "text": "Enhanced due diligence is mandatory when customer risk_score >= 70.",
     "check": lambda c: ("edd_required", True) if c.get("risk_score", 0) >= 70 else None},
    {"id": "KYC-014", "text": "Politically exposed persons (PEP) always require compliance officer approval.",
     "check": lambda c: ("compliance_approval", True) if c.get("pep") else None},
    {"id": "LIM-203", "text": "Single-transaction exposure above EUR 100,000 requires VP approval.",
     "check": lambda c: ("vp_approval", True) if c.get("exposure_eur", 0) > 100_000 else None},
    {"id": "JUR-077", "text": "Customers in sanctioned jurisdictions must be rejected.",
     "check": lambda c: ("reject", True) if c.get("jurisdiction") in {"SANCTIONED-X"} else None},
    {"id": "DOC-031", "text": "Onboarding without verified identity documents may not proceed past draft.",
     "check": lambda c: ("hold_draft", True) if not c.get("id_verified", False) else None},
]


def register(mcp):
    @mcp.tool()
    def evaluate_rules(case: dict) -> dict:
        """Deterministically evaluate an onboarding case against mandatory controls.
        Input keys: risk_score:int, pep:bool, exposure_eur:number, jurisdiction:str,
        id_verified:bool. Returns triggered rule ids + obligations. Same input,
        same output, every time — this is the guarantee layer."""
        triggered, obligations = [], {}
        for r in RULES:
            hit = r["check"](case)
            if hit:
                triggered.append({"id": r["id"], "text": r["text"]})
                obligations[hit[0]] = hit[1]
        permitted = not obligations.get("reject", False)
        return {"permitted": permitted, "triggered_rules": triggered,
                "obligations": obligations,
                "rule_version": "2026-07-01"}

    @mcp.tool()
    def list_rules() -> list[dict]:
        """List the current mandatory-control rule set (id + text)."""
        return [{"id": r["id"], "text": r["text"]} for r in RULES]
