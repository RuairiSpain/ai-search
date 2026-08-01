"""Graph reasoning (§15): the model plans traversals, code executes them,
and the relationship map is both the evidence and the explanation.

Loop shape: planner emits one traversal action per turn (a micro-ReAct where
the action space is graph operations); the traversal log accumulates; the
synthesist writes the verdict citing explicit entity-relation-entity chains.
The docs-only variant removes the tools and hands over flat entity records —
the ablation that shows why relationship problems aren't document problems.
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

ONTOLOGY = (PATTERN_DIR / "ontology.yaml").read_text(encoding="utf-8")
ALLOWED_ACTIONS = {"get_entity", "get_neighbors", "find_paths"}


def _instr(name: str) -> str:
    return (PATTERN_DIR / "agents" / name / "instructions.md").read_text(encoding="utf-8")


def _skill() -> str:
    return (PATTERN_DIR / "skills" / "entity-resolution" / "SKILL.md").read_text(encoding="utf-8")


def _docs_dump() -> str:
    """docs-only ablation context: every entity as a flat 'document', no edges."""
    docs = {}
    for eid in ("A1", "A2", "A3", "A4", "A5", "A6"):
        docs[eid] = call_mcp_tool("get_entity", {"entity_id": eid})
    return json.dumps(docs, indent=1)


def run_case(query: str, cfg: dict, ledger: CostLedger | None = None) -> dict:
    ledger = ledger or CostLedger(new_run_tag("p10"))
    telemetry_init("pattern-10-graph")
    budgets_cfg = load_budgets(PATTERN_DIR)
    budget = Budget.from_config(budgets_cfg, label="p10")
    trace: dict = {"hops": [], "shields": [], "shield_log": []}

    if not cfg.get("graph_tools", True):
        # ---- docs-only ablation: same question, no traversal ----------------
        with span("p10.docs_only", variant=cfg["_variant_name"]):
            budget.charge()
            res = fc.chat(cfg["synth_deployment"], [
                {"role": "system", "content": _instr("synthesist") + "\n\n" + _skill()
                 + "\n\nNOTE: no traversal tools are available; answer from the "
                   "documents alone and be honest about what you cannot know."},
                {"role": "user", "content": f"Question:\n{query}\n\nAccount documents:\n{_docs_dump()}"},
            ], max_output_tokens=700)
            ledger.add_result(res, "docs_only")
        return {"response": res.text, "trace": {**trace, "mode": "docs_only",
                                                "budget": budget.snapshot()}}

    traversal_log: list[dict] = []
    max_hops = budgets_cfg.get("max_traversal_hops", 12)
    try:
        with span("p10.traverse", variant=cfg["_variant_name"]):
            for hop in range(max_hops):
                budget.charge()
                step, res = fc.chat_json(cfg["planner_deployment"], [
                    {"role": "system", "content": _instr("traversal-planner") + "\n\n"
                     + _skill() + f"\n\nOntology:\n{ONTOLOGY}"},
                    {"role": "user", "content": (f"Question:\n{query}\n\n"
                                                 f"Traversal log so far:\n{json.dumps(traversal_log, indent=1)}")},
                ], max_output_tokens=250)
                budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
                ledger.add_result(res, f"plan_hop[{hop}]")

                action = step.get("action", "conclude")
                if action == "conclude":
                    trace["conclude_reason"] = step.get("why", "")
                    break
                if action not in ALLOWED_ACTIONS:
                    traversal_log.append({"error": f"disallowed action {action}"})
                    continue
                obs = call_mcp_tool(action, step.get("args", {}))
                if cfg.get("shield_observations", True):
                    shield = shield_check([json.dumps(obs)], user_prompt=query)
                    trace["shield_log"].append({"hop": hop, "action": action, **shield})
                    if shield["attack_detected"]:
                        trace["shields"].append({"hop": hop, "action": action,
                                                 "attack_detected": True})
                entry = {"hop": hop, "action": action, "args": step.get("args", {}),
                         "why": step.get("why", ""), "observation": obs}
                traversal_log.append(entry)
                trace["hops"].append({"hop": hop, "action": action,
                                      "args": step.get("args", {})})

        with span("p10.synthesise", hops=len(traversal_log)):
            budget.charge()
            res = fc.chat(cfg["synth_deployment"], [
                {"role": "system", "content": _instr("synthesist") + "\n\n" + _skill()},
                {"role": "user", "content": (f"Question:\n{query}\n\n"
                                             f"Traversal log:\n{json.dumps(traversal_log, indent=1)}")},
            ], max_output_tokens=800)
            budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
            ledger.add_result(res, "synthesise")

        response = res.text
        if trace["shields"]:
            response += ("\n\nNote: Prompt Shields flagged instruction-like content in "
                         f"{len(trace['shields'])} observation(s); treated as data.")
        unchecked = [s for s in trace["shield_log"] if not s["checked"]]
        if unchecked:
            response += (f"\n\nWARNING: Prompt Shields could not be reached for "
                         f"{len(unchecked)} observation(s) — injection detection was NOT "
                         f"performed on those (reason: {unchecked[0]['reason']}). Treat this "
                         "run's evidence with extra scrutiny.")
        return {"response": response, "trace": {**trace, "budget": budget.snapshot()}}

    except BudgetExceeded as e:
        return {"response": (f"INCONCLUSIVE: budget exhausted after {len(traversal_log)} hops "
                             f"({e}). Partial chains in trace; recommend an analyst continues "
                             "from the traversal log."),
                "trace": {**trace, "budget": budget.snapshot(), "escalated": True}}


if __name__ == "__main__":
    from reasoning_common.config import load_variant
    cfg = load_variant(PATTERN_DIR)
    sample = json.loads((PATTERN_DIR / "data" / "sample_input.json").read_text())
    ledger = CostLedger(new_run_tag(f"p10-{cfg['_variant_name']}"))
    out = run_case(sample["query"], cfg, ledger)
    print(out["response"])
    print("\n--- hops ---\n" + json.dumps(out["trace"].get("hops", []), indent=2))
    print("\n--- cost ---\n" + json.dumps(ledger.summary()["by_deployment"], indent=2))
    ledger.dump(PATTERN_DIR / "runs")
