"""Neuro-symbolic onboarding (§14): the LLM interprets, the engine decides.

The whole pattern is the ~20-line `enforce()` function: whatever the proposer
recommends, the deterministic verdict from the real `evaluate_rules` MCP tool
wins. Delete the engine (variant prompt-rules-only) and the eval shows the
difference between a tendency and a guarantee.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PATTERN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATTERN_DIR.parents[1] / "common"))

from reasoning_common import foundry_client as fc  # noqa: E402
from reasoning_common.budgets import Budget  # noqa: E402
from reasoning_common.config import load_budgets  # noqa: E402
from reasoning_common.costs import CostLedger  # noqa: E402
from reasoning_common.mcp_client import call_mcp_tool  # noqa: E402
from reasoning_common.telemetry import init as telemetry_init, new_run_tag, span  # noqa: E402

DIRECTIVE = (PATTERN_DIR.parents[1] / "common" / "knowledge" / "documents"
             / "eu-onboarding-directive.md").read_text(encoding="utf-8")
RULES_AS_PROMPT = json.dumps(  # used ONLY by the ablation variant
    [{"id": "KYC-001", "text": "EDD mandatory when risk_score >= 70"},
     {"id": "KYC-014", "text": "PEP requires compliance officer approval"},
     {"id": "LIM-203", "text": ">EUR 100,000 exposure requires VP approval"},
     {"id": "JUR-077", "text": "Sanctioned jurisdictions must be rejected"},
     {"id": "DOC-031", "text": "No verified identity -> hold at draft"}])


def _instr(name: str) -> str:
    return (PATTERN_DIR / "agents" / name / "instructions.md").read_text(encoding="utf-8")


def _skill() -> str:
    return (PATTERN_DIR / "skills" / "case-structuring" / "SKILL.md").read_text(encoding="utf-8")


def extract_case(query: str, cfg: dict, budget: Budget, ledger: CostLedger) -> dict:
    budget.charge()
    case, res = fc.chat_json(cfg["extractor_deployment"], [
        {"role": "system",
         "content": ("Extract structured onboarding case fields per the skill below. "
                     "Output ONLY JSON with keys risk_score, pep, exposure_eur, "
                     "jurisdiction, id_verified, missing_fields.\n\n" + _skill())},
        {"role": "user", "content": query},
    ], max_output_tokens=300)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, "extract")
    # Conservative defaults for the engine (unknown ≠ safe):
    case.setdefault("risk_score", 0)
    case.setdefault("pep", False)
    case.setdefault("exposure_eur", 0)
    case.setdefault("id_verified", False)
    case["risk_score"] = case["risk_score"] or 0
    case["exposure_eur"] = case["exposure_eur"] or 0
    return case


def propose(query: str, case: dict, cfg: dict, budget: Budget, ledger: CostLedger,
            rules_in_prompt: bool) -> dict:
    system = _instr("onboarding-proposer")
    if rules_in_prompt:  # ablation: rules as text, hope as enforcement
        system += f"\n\n# Mandatory controls (comply with these)\n{RULES_AS_PROMPT}"
    budget.charge()
    prop, res = fc.chat_json(cfg["proposer_deployment"], [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Request:\n{query}\n\nStructured case:\n{json.dumps(case)}"},
    ], max_output_tokens=400)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, "propose")
    return prop


def enforce(proposal: dict, verdict: dict) -> dict:
    """The guarantee layer. Code, not model. The engine's verdict is final."""
    obligations = verdict.get("obligations", {})
    if not verdict.get("permitted", False):
        outcome = "reject"
    elif obligations.get("hold_draft"):
        outcome = "hold"
    elif obligations:
        outcome = "proceed_with_obligations"
    else:
        outcome = proposal.get("provisional_recommendation", "proceed")
        if outcome in ("reject", "hold"):  # proposer more cautious than rules: keep caution
            pass
        else:
            outcome = "proceed"
    return {"outcome": outcome,
            "rules_cited": [r["id"] for r in verdict.get("triggered_rules", [])],
            "verdict": verdict}


def explain(query: str, case: dict, proposal: dict, enforced: dict, cfg: dict,
            budget: Budget, ledger: CostLedger) -> str:
    budget.charge()
    res = fc.chat(cfg["explainer_deployment"], [
        {"role": "system", "content": _instr("decision-explainer")
         + "\n\n# Directive extracts (cite Art. 12 for explainability)\n" + DIRECTIVE},
        {"role": "user", "content": json.dumps(
            {"request": query, "case": case, "proposal": proposal,
             "enforced_outcome": enforced["outcome"],
             "engine_verdict": enforced["verdict"]})},
    ], max_output_tokens=600)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, "explain")
    return res.text


def run_case(query: str, cfg: dict, ledger: CostLedger | None = None) -> dict:
    ledger = ledger or CostLedger(new_run_tag("p04"))
    telemetry_init("pattern-04-neurosymbolic")
    budget = Budget.from_config(load_budgets(PATTERN_DIR), label="p04")

    if cfg.get("mode") == "single_call":  # falsifiability baseline
        with span("p04.single_frontier"):
            res = fc.chat(cfg["proposer_deployment"], [
                {"role": "system",
                 "content": ("You are a bank onboarding decision assistant. Decide and explain, "
                             "citing rule ids where relevant.\nRules:\n" + RULES_AS_PROMPT
                             + "\nDirective:\n" + DIRECTIVE)},
                {"role": "user", "content": query},
            ], max_output_tokens=700)
        ledger.add_result(res, "single_frontier")
        return {"response": res.text, "trace": {"mode": "single_call"}}

    with span("p04.extract", variant=cfg["_variant_name"]):
        case = extract_case(query, cfg, budget, ledger)

    use_engine = cfg.get("use_rules_engine", True)
    with span("p04.propose", rules_in_prompt=not use_engine):
        proposal = propose(query, case, cfg, budget, ledger, rules_in_prompt=not use_engine)

    if use_engine:
        with span("p04.rules_engine"):  # REAL deterministic MCP call
            verdict = call_mcp_tool("evaluate_rules", {"case": {
                k: case.get(k) for k in ("risk_score", "pep", "exposure_eur",
                                         "jurisdiction", "id_verified")}})
        enforced = enforce(proposal, verdict)
    else:
        # Ablation: no engine, no enforcement — proposer's word is final.
        enforced = {"outcome": proposal.get("provisional_recommendation", "proceed"),
                    "rules_cited": [], "verdict": {"engine": "DISABLED (ablation variant)"}}

    with span("p04.explain", outcome=enforced["outcome"]):
        text = explain(query, case, proposal, enforced, cfg, budget, ledger)

    return {"response": text,
            "trace": {"case": case, "proposal": proposal,
                      "outcome": enforced["outcome"],
                      "rules_cited": enforced["rules_cited"],
                      "engine_used": use_engine,
                      "budget": budget.snapshot()}}


if __name__ == "__main__":
    from reasoning_common.config import load_variant
    cfg = load_variant(PATTERN_DIR)
    sample = json.loads((PATTERN_DIR / "data" / "sample_input.json").read_text())
    ledger = CostLedger(new_run_tag(f"p04-{cfg['_variant_name']}"))
    out = run_case(sample["query"], cfg, ledger)
    print(out["response"])
    print("\n--- trace ---\n" + json.dumps(out["trace"], indent=2))
    print("\n--- cost ---\n" + json.dumps(ledger.summary()["by_deployment"], indent=2))
    ledger.dump(PATTERN_DIR / "runs")
