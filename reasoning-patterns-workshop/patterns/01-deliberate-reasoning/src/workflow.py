"""Deliberate reasoning (§5): generate → evaluate → select, budget-bounded.

Design points this file demonstrates:
- Deterministic check BEFORE any model judge (§3 "prefer checks the business trusts").
- Judge on a cheaper deployment than the generator (§12 role/model table).
- BudgetExceeded escalates to a single frontier call instead of dying (§18).
- Rejected candidates are part of the output — evidence for the human, and
  eval-able ("did it log alternatives?").

`run_case(query, cfg, ledger)` is the contract the shared eval runner calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PATTERN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATTERN_DIR.parents[1] / "common"))

from reasoning_common import foundry_client as fc  # noqa: E402
from reasoning_common.budgets import Budget, BudgetExceeded  # noqa: E402
from reasoning_common.config import load_budgets  # noqa: E402
from reasoning_common.costs import CostLedger  # noqa: E402
from reasoning_common.telemetry import init as telemetry_init, new_run_tag, span  # noqa: E402

RUNBOOK = (PATTERN_DIR.parents[1] / "common" / "knowledge" / "documents"
           / "contoso-diagnostics-runbook.md").read_text(encoding="utf-8")


def _load_instructions(cfg: dict) -> str:
    parts = [(PATTERN_DIR / cfg["instructions_file"]).read_text(encoding="utf-8")]
    for s in cfg.get("skills", []):
        parts.append((PATTERN_DIR / s).read_text(encoding="utf-8"))
    return "\n\n---\n\n".join(parts)


# ------------------------------------------------------------------ evaluators
def deterministic_check(candidate: dict) -> tuple[bool, str]:
    """RB-7 compliance — a rule the business already trusts. Free. Unfoolable."""
    if not isinstance(candidate, dict) or "hypothesis" not in candidate:
        return False, "malformed candidate (schema violation)"
    if not candidate.get("pool_metrics_addressed", False):
        txt = json.dumps(candidate).lower()
        if "pool" not in txt:
            return False, "RB-7 rule 1 violated: connection-pool check neither done nor deferred with reason"
    if not candidate.get("evidence"):
        return False, "no evidence cited"
    return True, "ok"


def judge(candidate: dict, query: str, cfg: dict, budget: Budget, ledger: CostLedger) -> tuple[float, str]:
    """Cheap LLM judge for what rules can't express: evidence alignment."""
    scored, res = fc.chat_json(cfg["judge_deployment"], [
        {"role": "system",
         "content": ("Score how well a diagnostic hypothesis is supported by the incident "
                     "evidence and runbook. Reply JSON: {\"score\": 0-10, \"reason\": str}. "
                     "Penalise confident causes lacking discriminating evidence.")},
        {"role": "user",
         "content": f"Incident:\n{query}\n\nRunbook:\n{RUNBOOK}\n\nCandidate:\n{json.dumps(candidate)}"},
    ], max_output_tokens=300)
    budget.charge(tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, step="judge")
    return float(scored.get("score", 0)), str(scored.get("reason", ""))


# ------------------------------------------------------------------ main flow
def run_case(query: str, cfg: dict, ledger: CostLedger | None = None) -> dict:
    ledger = ledger or CostLedger(new_run_tag("p01"))
    telemetry_init("pattern-01-deliberate")

    if cfg.get("mode") == "single_call":  # falsifiability baseline variant
        return _single_frontier(query, cfg, ledger, deployment=cfg.get("generator_deployment", "frontier"))

    budget = Budget.from_config(load_budgets(PATTERN_DIR), label="p01-deliberate")
    instructions = _load_instructions(cfg)
    accepted: list[dict] = []
    rejected: list[dict] = []

    try:
        with span("deliberate.generate", n=cfg["n_candidates"], variant=cfg["_variant_name"]):
            proposed_summaries: list[str] = []
            for i in range(cfg["n_candidates"]):
                budget.charge()  # count the call before making it
                cand, res = fc.chat_json(cfg["generator_deployment"], [
                    {"role": "system", "content": instructions},
                    {"role": "user",
                     "content": (f"Incident:\n{query}\n\nRunbook context:\n{RUNBOOK}\n\n"
                                 f"Already proposed: {proposed_summaries or 'none'}")},
                ], temperature=0.9, max_output_tokens=500)
                budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
                ledger.add_result(res, step=f"generate[{i}]")
                proposed_summaries.append(cand.get("hypothesis", "")[:120])

                ok, why = deterministic_check(cand)  # rules BEFORE judges
                if not ok:
                    rejected.append({"candidate": cand, "stage": "deterministic", "reason": why})
                    continue
                score, reason = judge(cand, query, cfg, budget, ledger)
                accepted.append({"candidate": cand, "score": score, "judge_reason": reason})
    except BudgetExceeded as e:
        with span("deliberate.escalate", reason=str(e)):
            out = _single_frontier(query, cfg, ledger, deployment="frontier")  # escalate UP, never sideways
            out["trace"]["escalated_from_budget"] = str(e)
            return out

    if not accepted:  # every candidate failed rules → escalate, don't guess
        out = _single_frontier(query, cfg, ledger, deployment="frontier")
        out["trace"]["escalated_all_rejected"] = [r["reason"] for r in rejected]
        return out

    best = max(accepted, key=lambda a: a["score"])
    alternatives = [a for a in accepted if a is not best]
    response = _render(best, alternatives, rejected)
    return {"response": response,
            "trace": {"budget": budget.snapshot(),
                      "accepted": len(accepted), "rejected": len(rejected),
                      "best_score": best["score"],
                      "rejected_reasons": [r["reason"] for r in rejected]}}


def _single_frontier(query: str, cfg: dict, ledger: CostLedger, *, deployment: str = "frontier") -> dict:
    instr = (PATTERN_DIR / "agents/baseline-diagnostician/instructions.md").read_text()
    with span("baseline.single_call", deployment=deployment):
        res = fc.chat(deployment,
                      [{"role": "system", "content": instr},
                       {"role": "user", "content": f"Incident:\n{query}\n\nRunbook:\n{RUNBOOK}"}],
                      max_output_tokens=700)
    ledger.add_result(res, step="single_frontier")
    return {"response": res.text, "trace": {"mode": "single_call"}}


def _render(best: dict, alternatives: list[dict], rejected: list[dict]) -> str:
    c = best["candidate"]
    lines = [
        f"Recommended next diagnostic step: {c.get('first_diagnostic_step', '(missing)')}",
        f"Leading hypothesis (score {best['score']}/10): {c.get('hypothesis')}",
        f"Evidence: {'; '.join(c.get('evidence', []))}",
        f"Judge rationale: {best['judge_reason']}",
    ]
    if alternatives:
        lines.append("Alternatives considered: " + " | ".join(
            f"{a['candidate'].get('hypothesis', '')[:100]} (score {a['score']})" for a in alternatives))
    if rejected:
        lines.append("Rejected by compliance check: " + " | ".join(r["reason"] for r in rejected))
    return "\n".join(lines)


if __name__ == "__main__":  # `make run` entrypoint
    from reasoning_common.config import load_variant
    cfg = load_variant(PATTERN_DIR)
    sample = json.loads((PATTERN_DIR / "data" / "sample_input.json").read_text())
    ledger = CostLedger(new_run_tag(f"p01-{cfg['_variant_name']}"))
    out = run_case(sample["query"], cfg, ledger)
    print(out["response"])
    print("\n--- trace ---\n" + json.dumps(out["trace"], indent=2))
    print("\n--- cost ---\n" + json.dumps(ledger.summary()["by_deployment"], indent=2))
    ledger.dump(PATTERN_DIR / "runs")
