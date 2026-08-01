"""Branching hypotheses (§8): five live at once, evidence discriminates,
commitment is delayed until it does.

Loop shape: hypotheses → per-round branch expansion (each branch picks ONE
discriminating tool call in parallel) → score → prune → repeat, bounded by
budgets. The `steerable` variant pauses at each prune boundary so an analyst
can kill/boost branches — steering the search budget itself.
"""
from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
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

ALLOWED_TOOLS = {"get_auth_events", "get_travel_records", "get_oauth_grants",
                 "get_mailbox_rules", "get_prior_incidents"}


def _instr(name: str) -> str:
    return (PATTERN_DIR / "agents" / name / "instructions.md").read_text(encoding="utf-8")


def _skill() -> str:
    return (PATTERN_DIR / "skills" / "hypothesis-discipline" / "SKILL.md").read_text(encoding="utf-8")


def _expand(branch: dict, query: str, cfg: dict, budget: Budget, ledger: CostLedger,
            trace: dict, trace_lock: "threading.Lock") -> dict:
    """One evidence step for one branch. Called from parallel workers.

    `trace` is the ONE piece of state shared across worker threads (each
    worker's own `branch` dict is distinct, so no lock is needed there);
    trace_lock guards the shields-list append below.
    """
    if branch.get("resolved"):
        return branch
    budget.charge()
    step, res = fc.chat_json(cfg["assessor_deployment"], [
        {"role": "system", "content": _instr("evidence-assessor") + "\n\n" + _skill()},
        {"role": "user", "content": (f"Alert:\n{query}\n\nThis hypothesis:\n"
                                     f"{json.dumps(branch['hypothesis'])}\n\n"
                                     f"Log for this branch so far:\n{json.dumps(branch['log'], indent=1)}")},
    ], max_output_tokens=250)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, f"expand[{branch['hypothesis']['id']}]")
    if step.get("action") == "resolved":
        branch["resolved"] = True
        branch["log"].append({"resolved_reason": step.get("assessment", "")})
        return branch
    tool = step.get("tool", "")
    if tool not in ALLOWED_TOOLS:
        branch["log"].append({"error": f"disallowed tool {tool}"})
        return branch
    obs = call_mcp_tool(tool, step.get("args", {}))
    if cfg.get("shield_observations", True):
        shield = shield_check([json.dumps(obs)], user_prompt=query)
        with trace_lock:
            trace.setdefault("shield_log", []).append(
                {"branch": branch["hypothesis"]["id"], "tool": tool, **shield})
            if shield["attack_detected"]:
                trace.setdefault("shields", []).append(
                    {"branch": branch["hypothesis"]["id"], "tool": tool})
    branch["log"].append({"tool": tool, "args": step.get("args", {}),
                          "assessment": step.get("assessment", ""), "observation": obs})
    return branch


def _score_branch(branch: dict, cfg: dict, budget: Budget, ledger: CostLedger) -> float:
    budget.charge()
    data, res = fc.chat_json(cfg["scorer_deployment"], [
        {"role": "system", "content":
         "Score 0-10 how well this branch is SUPPORTED by its evidence log AND "
         "how DISCRIMINATING that evidence is (evidence that fits many "
         "hypotheses is less discriminating). JSON: {\"score\": n, \"why\": str}."},
        {"role": "user", "content": json.dumps({"hypothesis": branch["hypothesis"],
                                                "log": branch["log"]})},
    ], max_output_tokens=150)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, f"score[{branch['hypothesis']['id']}]")
    return float(data.get("score", 0))


def select_steer(cfg: dict, explicit=None, *, interactive: bool = False):
    """Injection wins; config never implies interactivity (see patterns 02/03/08)."""
    if explicit is not None:
        return explicit
    if interactive and cfg.get("steerable") and sys.stdin.isatty():
        return _cli_prune_steer
    return None


def _cli_prune_steer(round_no: int, ranked: list[dict]) -> dict:
    """Reference steering UI: kill/boost branches at a prune boundary."""
    if not sys.stdin.isatty():
        return {"action": "continue"}   # never block a headless run
    print(f"\n⏸  PRUNE BOUNDARY — round {round_no}. Surviving branches:")
    for b in ranked:
        h = b["hypothesis"]
        print(f"   [{h['id']}] score {b['_score']:.1f} — {h['mechanism'][:80]}")
    print("   options: [Enter]=continue  k <ids>=kill  b <id>=boost (protect from prune)  e <reason>=escalate")
    raw = input("   > ").strip()
    if not raw:
        return {"action": "continue"}
    cmd, _, rest = raw.partition(" ")
    if cmd == "k":
        return {"action": "kill", "ids": set(rest.split())}
    if cmd == "b":
        return {"action": "boost", "id": rest.strip()}
    if cmd == "e":
        return {"action": "escalate", "detail": rest or "analyst escalation"}
    return {"action": "continue"}


def run_case(query: str, cfg: dict, ledger: CostLedger | None = None,
             steer=None) -> dict:
    ledger = ledger or CostLedger(new_run_tag("p05"))
    telemetry_init("pattern-05-branching")
    budgets_cfg = load_budgets(PATTERN_DIR)
    budget = Budget.from_config(budgets_cfg, label="p05")
    trace: dict = {"rounds": [], "interventions": []}
    trace_lock = threading.Lock()  # the ONE piece of state _expand's workers share

    if cfg.get("mode") == "single_call":
        with span("p05.single_frontier"):
            res = fc.chat(cfg["synth_deployment"], [
                {"role": "system", "content":
                 ("You are a SOC analyst. Investigate the alert against user data. "
                  "You have no tools; reason from first principles about likely causes, "
                  "list what evidence would resolve them, and give your best hypothesis.")},
                {"role": "user", "content": query},
            ], max_output_tokens=800)
        ledger.add_result(res, "single_frontier")
        return {"response": res.text, "trace": {**trace, "mode": "single_call"}}

    try:
        with span("p05.generate", n=cfg["n_hypotheses"]):
            budget.charge()
            data, res = fc.chat_json(cfg["hypo_deployment"], [
                {"role": "system", "content": _instr("hypothesis-generator") + "\n\n" + _skill()},
                {"role": "user", "content": f"Alert:\n{query}\n\nGenerate {cfg['n_hypotheses']} hypotheses."},
            ], max_output_tokens=500)
            budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
            ledger.add_result(res, "generate_hypotheses")
        branches = [{"hypothesis": h, "log": [], "resolved": False,
                     "boosted": False, "_score": 0.0}
                    for h in data.get("hypotheses", [])]
        trace["hypotheses"] = [b["hypothesis"] for b in branches]

        keep = cfg.get("keep_top_k_override", budgets_cfg.get("keep_top_k", 3))
        threshold = budgets_cfg.get("prune_below_score", 3.0)
        for round_no in range(budgets_cfg.get("max_rounds", 3)):
            live = [b for b in branches if not b.get("killed")]
            with span("p05.expand", round=round_no, live=len(live)):
                with ThreadPoolExecutor(max_workers=min(5, len(live) or 1)) as ex:
                    # _expand mutates each branch dict in place and returns it;
                    # list(...) forces the executor to wait for every branch
                    # before continuing (same effect as a `for _ in ex.map(...):
                    # pass` loop, without a body that reads as doing nothing).
                    # Each worker touches only its OWN branch dict — no two
                    # threads share a mutable key there — except `trace`, which
                    # _expand guards with trace_lock (the shields list is the
                    # only thing multiple workers can write to concurrently).
                    list(ex.map(lambda b: _expand(b, query, cfg, budget, ledger, trace, trace_lock), live))
            with span("p05.score", round=round_no):
                for b in live:
                    b["_score"] = _score_branch(b, cfg, budget, ledger)
            ranked = sorted(live, key=lambda b: -b["_score"])
            round_snapshot = {"round": round_no,
                              "scores": [{"id": b["hypothesis"]["id"], "score": b["_score"]}
                                         for b in ranked]}

            if steer is not None:
                decision = steer(round_no, ranked)
                trace["interventions"].append(decision)
                if decision.get("action") == "escalate":
                    return {"response": f"ESCALATED BY ANALYST at round {round_no}: "
                                        f"{decision.get('detail', '')}",
                            "trace": {**trace, "budget": budget.snapshot(),
                                      "rounds": trace["rounds"] + [round_snapshot]}}
                if decision.get("action") == "kill":
                    ids = decision.get("ids", set())
                    for b in ranked:
                        if b["hypothesis"]["id"] in ids:
                            b["killed"] = True
                if decision.get("action") == "boost":
                    for b in ranked:
                        if b["hypothesis"]["id"] == decision.get("id"):
                            b["boosted"] = True

            if not cfg.get("disable_pruning"):
                survivors, pruned = [], []
                for i, b in enumerate(ranked):
                    if b.get("boosted") or i < keep:
                        if b["_score"] >= threshold or b.get("boosted"):
                            survivors.append(b)
                        else:
                            pruned.append(b)
                    else:
                        pruned.append(b)
                for b in pruned:
                    b["killed"] = True
                round_snapshot["pruned"] = [b["hypothesis"]["id"] for b in pruned]
            trace["rounds"].append(round_snapshot)

            if sum(1 for b in branches if not b.get("killed")) <= 1:
                break

        with span("p05.synthesise"):
            budget.charge()
            res = fc.chat(cfg["synth_deployment"], [
                {"role": "system", "content": _instr("verdict-synthesist") + "\n\n" + _skill()},
                {"role": "user", "content": json.dumps({"alert": query, "branches": branches})},
            ], max_output_tokens=900)
            budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
            ledger.add_result(res, "synthesise")

        response = res.text
        if trace.get("shields"):
            response += (f"\n\nNote: Prompt Shields flagged instruction-like content in "
                         f"{len(trace['shields'])} observation(s); treated as data.")
        unchecked = [s for s in trace.get("shield_log", []) if not s["checked"]]
        if unchecked:
            response += (f"\n\nWARNING: Prompt Shields could not be reached for "
                         f"{len(unchecked)} observation(s) — injection detection was NOT "
                         f"performed on those (reason: {unchecked[0]['reason']}). Treat this "
                         "run's evidence with extra scrutiny.")
        return {"response": response, "trace": {**trace, "budget": budget.snapshot()}}

    except BudgetExceeded as e:
        return {"response": f"BUDGET EXHAUSTED at round {len(trace['rounds'])}: {e}. "
                            f"Report surviving branches and required evidence to SOC lead.",
                "trace": {**trace, "budget": budget.snapshot(), "escalated": True}}


if __name__ == "__main__":
    import argparse
    from reasoning_common.config import load_variant
    ap = argparse.ArgumentParser()
    ap.add_argument("--interactive", action="store_true",
                    help="pause at prune boundaries (VARIANT=steerable)")
    args = ap.parse_args()
    cfg = load_variant(PATTERN_DIR)
    sample = json.loads((PATTERN_DIR / "data" / "sample_input.json").read_text())
    ledger = CostLedger(new_run_tag(f"p05-{cfg['_variant_name']}"))
    out = run_case(sample["query"], cfg, ledger,
                   steer=select_steer(cfg, None, interactive=args.interactive))
    print(out["response"])
    print("\n--- rounds ---\n" + json.dumps(out["trace"]["rounds"], indent=2))
    print("\n--- cost ---\n" + json.dumps(ledger.summary()["by_deployment"], indent=2))
    ledger.dump(PATTERN_DIR / "runs")
