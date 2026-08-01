"""Search-based reasoning (§7): generate → constrain → score → deepen top-k.

The economics ARE the pattern: invalid candidates die on free deterministic
checks; only survivors get a nano score; only the top-k get deep analysis.
The no-precheck variant deletes the free kill so `make cost` can show what
that discipline is worth.

run_case(query, cfg, ledger) is the shared eval-runner contract.
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
from reasoning_common.mcp_client import call_mcp_tool  # noqa: E402
from reasoning_common.safety import shield_check  # noqa: E402
from reasoning_common.telemetry import init as telemetry_init, new_run_tag, span  # noqa: E402


def _instr(name: str) -> str:
    return (PATTERN_DIR / "agents" / name / "instructions.md").read_text(encoding="utf-8")


def _skill() -> str:
    return (PATTERN_DIR / "skills" / "migration-constraints" / "SKILL.md").read_text(encoding="utf-8")


# ------------------------------------------------------------- constraint layer
def check_sequence(waves: list[list[str]], catalog: dict) -> tuple[bool, str]:
    """The free kill. Deterministic, versioned, and the reason search doesn't
    amplify garbage (§7: 'a poor evaluation function makes search worse')."""
    flat = [s for w in waves for s in w]
    if sorted(flat) != sorted(catalog.keys()):
        missing = set(catalog) - set(flat)
        extra = set(flat) - set(catalog)
        dup = {s for s in flat if flat.count(s) > 1}
        return False, f"completeness violated (missing={sorted(missing)}, extra={sorted(extra)}, dup={sorted(dup)})"
    wave_of = {s: i for i, w in enumerate(waves) for s in w}
    for s, meta in catalog.items():
        for dep in meta.get("depends_on", []):
            if wave_of[dep] > wave_of[s]:
                return False, f"dependency violated: {dep} (wave {wave_of[dep]}) after dependent {s} (wave {wave_of[s]})"
    if any(len(w) > 3 for w in waves):
        return False, "wave size > 3"
    for i, w in enumerate(waves):
        if any(catalog[s].get("downtime_window") == "none" for s in w):
            dependents_sun = [s for s in w if catalog[s].get("downtime_window") == "sun-02"
                              and any(d in w for d in catalog[s].get("depends_on", []))]
            if dependents_sun:
                return False, f"wave {i}: zero-downtime system shares wave with sun-02 dependent {dependents_sun}"
    return True, "ok"


# ---------------------------------------------------------------- search nodes
def _catalog_context(cfg: dict, trace: dict) -> tuple[dict, str]:
    catalog = call_mcp_tool("get_system_catalog", {})
    docs = [json.dumps({k: v}) for k, v in catalog.items()]
    if cfg.get("shield_observations", True):
        trace["prompt_shields"] = shield_check(docs)
        # Detection is logged, not silently scrubbed: the model must ALSO hold
        # the line (defence-in-depth), and the eval checks that it does.
    return catalog, json.dumps(catalog, indent=1)


def run_case(query: str, cfg: dict, ledger: CostLedger | None = None) -> dict:
    ledger = ledger or CostLedger(new_run_tag("p09"))
    telemetry_init("pattern-09-search")
    budget = Budget.from_config(load_budgets(PATTERN_DIR), label="p09")
    trace: dict = {"rejected": [], "scored": [], "deepened": []}

    if cfg.get("mode") == "single_call":
        catalog, cat_json = _catalog_context(cfg, trace)
        res = fc.chat(cfg["generator_deployment"], [
            {"role": "system", "content": _instr("baseline-planner") + "\n\n" + _skill()},
            {"role": "user", "content": f"Goal:\n{query}\n\nCatalog:\n{cat_json}"},
        ], max_output_tokens=900)
        ledger.add_result(res, "single_frontier")
        return {"response": res.text, "trace": {**trace, "mode": "single_call"}}

    try:
        catalog, cat_json = _catalog_context(cfg, trace)
        candidates: list[dict] = []
        proposed: list[str] = []
        with span("p09.generate", n=cfg["n_candidates"], variant=cfg["_variant_name"]):
            for i in range(cfg["n_candidates"]):
                budget.charge()
                cand, res = fc.chat_json(cfg["generator_deployment"], [
                    {"role": "system", "content": _instr("sequence-generator") + "\n\n" + _skill()},
                    {"role": "user", "content": (f"Goal:\n{query}\n\nCatalog:\n{cat_json}\n\n"
                                                 f"Already proposed: {proposed or 'none'}")},
                ], temperature=0.9, max_output_tokens=400)
                budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
                ledger.add_result(res, f"generate[{i}]")
                proposed.append(json.dumps(cand.get("waves", []))[:120])
                candidates.append(cand)

        survivors: list[dict] = []
        with span("p09.constrain", precheck=cfg["constraint_precheck"]):
            for cand in candidates:
                if cfg["constraint_precheck"]:
                    ok, why = check_sequence(cand.get("waves", []), catalog)
                    if not ok:
                        trace["rejected"].append(why)  # died FREE
                        continue
                survivors.append(cand)

        with span("p09.score", survivors=len(survivors)):
            for cand in survivors:
                budget.charge()
                scored, res = fc.chat_json(cfg["scorer_deployment"], [
                    {"role": "system",
                     "content": ("Score 0-10 how well this migration sequence balances risk, "
                                 "dependency safety and early value. JSON: {\"score\": n, \"reason\": str}. "
                                 "If the sequence violates any stated constraint, score 0.")},
                    {"role": "user", "content": f"Constraints:\n{_skill()}\n\nCatalog:\n{cat_json}\n\nSequence:\n{json.dumps(cand)}"},
                ], max_output_tokens=200)
                budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
                ledger.add_result(res, "score")
                cand["_score"] = float(scored.get("score", 0))
                cand["_score_reason"] = scored.get("reason", "")
                trace["scored"].append({"waves": cand.get("waves"), "score": cand["_score"]})

        if not survivors:
            return {"response": ("No valid sequence found: every candidate violated hard "
                                 "constraints. Violations: " + "; ".join(trace["rejected"][:6])
                                 + ". The constraint set may be infeasible as stated — escalate "
                                 "to the architecture board rather than force a plan."),
                    "trace": {**trace, "budget": budget.snapshot(), "infeasible": True}}

        top = sorted(survivors, key=lambda c: -c["_score"])[: cfg["deepen_top_k"]]
        with span("p09.deepen", k=len(top)):
            for cand in top:
                budget.charge()
                deep, res = fc.chat_json(cfg["deep_deployment"], [
                    {"role": "system", "content": _instr("deep-analyst")},
                    {"role": "user", "content": f"Catalog:\n{cat_json}\n\nSequence:\n{json.dumps({'waves': cand['waves']})}"},
                ], max_output_tokens=500)
                budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
                ledger.add_result(res, "deepen")
                cand["_deep"] = deep
                trace["deepened"].append({"waves": cand["waves"],
                                          "execution_risk": deep.get("execution_risk")})

        best = min(top, key=lambda c: (c.get("_deep", {}).get("execution_risk", 10), -c["_score"]))
        return {"response": _render(best, top, trace), 
                "trace": {**trace, "budget": budget.snapshot()}}

    except BudgetExceeded as e:
        return {"response": f"ESCALATED: reasoning budget exhausted mid-search ({e}). "
                            f"Partial results: {len(trace['scored'])} scored, best so far attached.",
                "trace": {**trace, "budget": budget.snapshot(), "escalated": True}}


def _render(best: dict, top: list[dict], trace: dict) -> str:
    alts = [c for c in top if c is not best]
    lines = [
        "Recommended sequence: " + " -> ".join("[" + ",".join(w) + "]" for w in best["waves"]),
        f"Strategy: {best.get('strategy', '')}",
        f"Breadth score {best['_score']}/10 ({best['_score_reason']}); "
        f"execution risk {best.get('_deep', {}).get('execution_risk', '?')}/10: "
        f"{best.get('_deep', {}).get('reason', '')}",
        "Key risks: " + "; ".join(f"wave {r['wave']}: {r['risk']} (mitigate: {r['mitigation']})"
                                   for r in best.get("_deep", {}).get("risks", [])[:4]),
    ]
    if alts:
        lines.append("Rejected alternatives (deep-analysed): " + " | ".join(
            " -> ".join("[" + ",".join(w) + "]" for w in a["waves"])
            + f" (risk {a.get('_deep', {}).get('execution_risk', '?')})" for a in alts))
    if trace["rejected"]:
        lines.append(f"Constraint-killed candidates: {len(trace['rejected'])} "
                     f"(e.g. {trace['rejected'][0]})")
    if trace.get("prompt_shields", {}).get("attack_detected"):
        lines.append("Note: Prompt Shields flagged instruction-like content in catalog "
                     "notes; it was treated as data.")
    elif trace.get("prompt_shields") and not trace["prompt_shields"]["checked"]:
        lines.append(f"WARNING: Prompt Shields could not be reached for this run "
                     f"(reason: {trace['prompt_shields']['reason']}) — injection "
                     "detection was NOT performed on catalog notes. Treat this "
                     "run's plan with extra scrutiny.")
    return "\n".join(lines)


if __name__ == "__main__":
    from reasoning_common.config import load_variant
    cfg = load_variant(PATTERN_DIR)
    sample = json.loads((PATTERN_DIR / "data" / "sample_input.json").read_text())
    ledger = CostLedger(new_run_tag(f"p09-{cfg['_variant_name']}"))
    out = run_case(sample["query"], cfg, ledger)
    print(out["response"])
    print("\n--- trace ---\n" + json.dumps(out["trace"], indent=2))
    print("\n--- cost ---\n" + json.dumps(ledger.summary()["by_deployment"], indent=2))
    ledger.dump(PATTERN_DIR / "runs")
