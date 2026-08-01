"""Pattern 03: planner → fan-out workers → cross-family reviewer → merger.

This module is the dependency-free implementation of the graph and is what
`make run`/`make eval` execute. `maf_workflow.py` expresses the IDENTICAL graph
with Microsoft Agent Framework executors/edges for study and for teams
standardising on MAF; both delegate to the same node functions below, so there
is exactly one copy of the business logic either way.

State sharing demonstrated: pydantic contracts on every edge + a Blob Storage
checkpoint of fan-out state (inspect it mid-run in the portal: storage account
→ container `p03-state`).
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PATTERN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATTERN_DIR.parents[1] / "common"))

from pydantic import ValidationError  # noqa: E402

from reasoning_common import foundry_client as fc  # noqa: E402
from reasoning_common.budgets import Budget, BudgetExceeded  # noqa: E402
from reasoning_common.config import load_budgets, shared_env  # noqa: E402
from reasoning_common.contracts import Decision, Plan, Review, WorkerOutput  # noqa: E402
from reasoning_common.costs import CostLedger  # noqa: E402
from reasoning_common.mcp_client import call_mcp_tool  # noqa: E402
from reasoning_common.telemetry import init as telemetry_init, new_run_tag, span  # noqa: E402


def _instr(name: str) -> str:
    return (PATTERN_DIR / "agents" / name / "instructions.md").read_text(encoding="utf-8")


def _checkpoint(run_tag: str, name: str, payload: dict) -> None:
    """Blob checkpoint — best-effort: state sharing demo, not a hard dependency."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
        acct = shared_env()["STORAGE_ACCOUNT"]
        svc = BlobServiceClient(f"https://{acct}.blob.core.windows.net", credential=DefaultAzureCredential())
        try:
            svc.create_container("p03-state")
        except Exception as e:
            # "create if not exists": only a real ResourceExistsError is
            # expected here. Anything else (auth, network, quota) is a
            # genuine problem and should surface, not vanish silently.
            from azure.core.exceptions import ResourceExistsError
            if not isinstance(e, ResourceExistsError):
                raise
        svc.get_blob_client("p03-state", f"{run_tag}/{name}.json").upload_blob(
            json.dumps(payload, indent=2), overwrite=True)
    except Exception as e:  # offline/local runs keep working
        print(f"  (checkpoint skipped: {type(e).__name__})", file=sys.stderr)


# --------------------------------------------------------------- graph nodes
def plan(query: str, cfg: dict, budget: Budget, ledger: CostLedger) -> Plan:
    budget.charge()
    data, res = fc.chat_json(cfg["planner_deployment"], [
        {"role": "system", "content": _instr("planner")},
        {"role": "user", "content": query},
    ], max_output_tokens=600)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, "plan")
    return Plan.model_validate(data)  # contract enforced at the boundary


def work(sub, upstream: dict[str, WorkerOutput], cfg: dict, budget: Budget,
         ledger: CostLedger) -> WorkerOutput:
    context = ""
    if sub.kind == "retrieve":
        # Grounding action is a REAL MCP call made by code, not by the model —
        # the model interprets; the tool fetches. Compare with pattern 02 where
        # the hosted agent calls the same server declaratively.
        segment = next((s for s in ("EU-retail", "US-retail", "EU-enterprise")
                        if s.lower() in sub.instruction.lower()), None)
        obs = call_mcp_tool("get_segment_metrics", {"segment": segment or sub.instruction})
        context = f"\nTool observation (get_segment_metrics): {json.dumps(obs)}"
    elif sub.depends_on:
        ups = {k: v.model_dump() for k, v in upstream.items() if k in sub.depends_on}
        context = f"\nUpstream results: {json.dumps(ups)}"

    budget.charge()
    data, res = fc.chat_json(cfg["worker_deployment"], [
        {"role": "system", "content": _instr("worker")},
        {"role": "user", "content": f"Subtask {sub.id} ({sub.kind}): {sub.instruction}{context}"},
    ], max_output_tokens=500)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, f"worker[{sub.id}]")
    data.setdefault("subtask_id", sub.id)
    return WorkerOutput.model_validate(data)


def review(outputs: list[WorkerOutput], goal: str, cfg: dict, budget: Budget,
           ledger: CostLedger) -> Review:
    budget.charge()
    data, res = fc.chat_json(cfg["reviewer_deployment"], [
        {"role": "system", "content": _instr("reviewer")},
        {"role": "user", "content": json.dumps(
            {"goal": goal, "worker_outputs": [o.model_dump() for o in outputs]})},
    ], max_output_tokens=500)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, "review")
    return Review.model_validate(data)


def merge(outputs: list[WorkerOutput], goal: str, cfg: dict, budget: Budget,
          ledger: CostLedger) -> Decision:
    budget.charge()
    data, res = fc.chat_json(cfg["merger_deployment"], [
        {"role": "system", "content": _instr("merger")},
        {"role": "user", "content": json.dumps(
            {"goal": goal, "approved_outputs": [o.model_dump() for o in outputs]})},
    ], max_output_tokens=600)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, "merge")
    return Decision.model_validate(data)


# --------------------------------------------------------------- entry point
def _run_via_maf(query: str, cfg: dict, ledger: CostLedger) -> dict:
    """Route through the MAF-native graph (src/maf_workflow.py) instead of
    the dependency-free implementation below. This is the ONLY place in the
    workshop where `make run`/`make eval` actually EXECUTE a Microsoft Agent
    Framework workflow — everywhere else maf_workflow.py exists for study
    (WorkflowViz, the request_info steering demo) but isn't on the executed
    path. Select it with `VARIANT=maf`.

    Local import: maf_workflow.py does `import workflow as impl` to reuse
    this module's plan/work/review/merge functions, so this module cannot
    import maf_workflow at module load time without a circular import.

    Known simplification vs. the non-MAF path: a BudgetExceeded raised by a
    MAF executor propagates as a plain exception (caught below, reported as
    an error) rather than the richer escalate-to-human handling the
    dependency-free run_case does. Wiring that distinction through MAF's
    executor/edge model is a real extension, not done here.
    """
    import asyncio
    import maf_workflow

    try:
        raw = asyncio.run(maf_workflow.run(query, cfg, ledger))
    except Exception as e:
        return {"response": f"__ERROR__ MAF graph failed: {type(e).__name__}: {e}",
                "trace": {"engine": "maf", "error": f"{type(e).__name__}: {e}"}}

    try:
        decision = json.loads(raw)
        response = (f"Recommendation: {decision.get('recommendation', '')}\n"
                    f"Evidence: {'; '.join(decision.get('evidence', []))}\n"
                    f"Rejected alternatives: "
                    f"{'; '.join(decision.get('rejected_alternatives', [])) or '(none listed)'}\n"
                    f"Confidence: {decision.get('confidence', '')}")
    except json.JSONDecodeError:
        decision, response = None, raw  # e.g. "(no output yielded)"

    return {"response": response, "trace": {"engine": "maf", "decision": decision}}


def run_case(query: str, cfg: dict, ledger: CostLedger | None = None,
             steer=None) -> dict:
    """steer: optional callable(stage, payload) -> decision dict, invoked at the
    two contract boundaries (see _cli_steer for the reference implementation).
    Steering happens at TYPED boundaries only — a Plan you can veto, a Review
    you can override — never mid-generation; that is what keeps every
    intervention loggable, evaluable and resumable. Wall clock pauses while
    the human thinks (Budget.human_wait)."""
    ledger = ledger or CostLedger(new_run_tag("p03"))
    telemetry_init("pattern-03-multiagent")

    if cfg.get("engine") == "maf":
        if steer is not None:
            print("WARN: steer= is ignored when engine=maf — MAF's steering path is "
                  "build_steerable()/run_steerable() in maf_workflow.py, a separate "
                  "mechanism (see README §7).", file=sys.stderr)
        return _run_via_maf(query, cfg, ledger)

    budgets_cfg = load_budgets(PATTERN_DIR)
    budget = Budget.from_config(budgets_cfg, label="p03")
    run_tag = ledger.run_tag
    trace: dict = {"review_rounds": [], "run_tag": run_tag, "interventions": []}

    def _steer(stage: str, payload: dict) -> dict:
        if steer is None:
            return {"action": "continue"}
        with budget.human_wait():
            decision = steer(stage, payload)
        record = {"stage": stage, "action": decision.get("action", "continue"),
                  "detail": decision.get("detail", "")}
        trace["interventions"].append(record)
        _append_intervention(run_tag, record)
        return decision

    try:
        with span("p03.plan", variant=cfg["_variant_name"], run_tag=run_tag):
            p = plan(query, cfg, budget, ledger)
        trace["plan"] = p.model_dump()
        _checkpoint(run_tag, "plan", trace["plan"])

        # ---- steering point 1: the Plan, before fan-out spends money -------
        d = _steer("plan", {"plan": p.model_dump()})
        if d.get("action") == "abort":
            return {"response": f"ABORTED BY USER at plan stage: {d.get('detail', '')}",
                    "trace": {**trace, "budget": budget.snapshot()}}
        if d.get("action") == "drop_subtasks":
            keep = [s for s in p.subtasks if s.id not in set(d.get("ids", []))]
            if keep:
                p = p.model_copy(update={"subtasks": keep})
                trace["plan_after_steer"] = p.model_dump()
        if d.get("action") == "edit_goal":
            p = p.model_copy(update={"goal": d.get("detail") or p.goal})

        guidance = ""
        for round_no in range(budgets_cfg.get("max_review_rounds", 2) + 1):
            with span("p03.fanout", round=round_no, run_tag=run_tag):
                done: dict[str, WorkerOutput] = {}
                ready = [s for s in p.subtasks if not s.depends_on]
                later = [s for s in p.subtasks if s.depends_on]
                q = ready
                with ThreadPoolExecutor(max_workers=cfg.get("max_workers_parallel", 3)) as ex:
                    for out in ex.map(lambda s: work(_with_guidance(s, guidance), done,
                                                     cfg, budget, ledger), q):
                        done[out.subtask_id] = out
                for s in later:  # dependency-ordered tail, sequential
                    done[s.id] = work(_with_guidance(s, guidance), done, cfg, budget, ledger)
            outputs = list(done.values())
            _checkpoint(run_tag, f"fanout-r{round_no}", {"outputs": [o.model_dump() for o in outputs]})

            with span("p03.review", round=round_no, run_tag=run_tag):
                r = review(outputs, p.goal, cfg, budget, ledger)
            trace["review_rounds"].append(r.model_dump())
            if r.verdict == "approve":
                break
            # ---- steering point 2: a revise verdict — whose guidance wins? --
            if r.verdict == "revise":
                d = _steer("review", {"review": r.model_dump(),
                                       "outputs": [o.model_dump() for o in outputs]})
                if d.get("action") == "force_approve":
                    trace["forced_approve"] = True
                    break
                if d.get("action") == "escalate":
                    return {"response": ("ESCALATED TO HUMAN by steering decision: "
                                         f"{d.get('detail', '')}"),
                            "trace": {**trace, "escalated": True,
                                      "budget": budget.snapshot()}}
                if d.get("action") == "replace_guidance":
                    r = r.model_copy(update={"revised_guidance": d.get("detail", "")})
            if r.verdict == "reject" or round_no >= budgets_cfg.get("max_review_rounds", 2):
                # Debate cap (§12): a third rejection is a human's problem now.
                return {"response": ("ESCALATED TO HUMAN: reviewer could not approve within "
                                     f"{round_no + 1} rounds. Issues: {'; '.join(r.issues)}"),
                        "trace": {**trace, "escalated": True, "budget": budget.snapshot()}}
            guidance = r.revised_guidance

        with span("p03.merge", run_tag=run_tag):
            d = merge(outputs, p.goal, cfg, budget, ledger)
        _checkpoint(run_tag, "decision", d.model_dump())
        response = (f"Recommendation: {d.recommendation}\n"
                    f"Evidence: {'; '.join(d.evidence)}\n"
                    f"Rejected alternatives: {'; '.join(d.rejected_alternatives) or '(none listed)'}\n"
                    f"Confidence: {d.confidence}")
        return {"response": response, "trace": {**trace, "budget": budget.snapshot()}}

    except BudgetExceeded as e:
        return {"response": f"ESCALATED TO HUMAN: reasoning budget exhausted ({e}).",
                "trace": {**trace, "escalated": True, "budget": budget.snapshot()}}
    except ValidationError as e:
        # Contract violation is a first-class, visible failure — not mush.
        return {"response": f"__ERROR__ contract violation between agents: {e.error_count()} issue(s)",
                "trace": {**trace, "contract_error": str(e)[:800]}}


def _append_intervention(run_tag: str, record: dict) -> None:
    """Interventions are DATA (§13): they join the eval set and, at volume,
    become tuning signal. A bare veto is noise; the CLI insists on reasons."""
    import time as _time
    path = PATTERN_DIR / "runs" / f"steer-{run_tag}.jsonl"
    path.parent.mkdir(exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({**record, "ts": _time.time()}) + "\n")


def select_steer(cfg: dict, explicit=None, *, interactive: bool = False):
    """Injection wins; config never implies interactivity. Same rule as
    patterns 02/05/08 so `make eval VARIANT=steerable` is always headless-safe."""
    if explicit is not None:
        return explicit
    if interactive and cfg.get("steerable") and sys.stdin.isatty():
        return _cli_steer
    return None


def _cli_steer(stage: str, payload: dict) -> dict:
    """Reference steering UI. Phase 2's branching pattern replaces this with
    streaming; pattern 08 replaces it with Durable external events."""
    if not sys.stdin.isatty():
        return {"action": "continue"}   # never block a headless run
    print(f"\n⏸  STEERING POINT: {stage}")
    if stage == "plan":
        for s in payload["plan"]["subtasks"]:
            print(f"   [{s['id']}] ({s['kind']}) {s['instruction']}")
        print("   options: [Enter]=continue  d <ids>=drop subtasks  g <text>=edit goal  a <reason>=abort")
        raw = input("   > ").strip()
        if not raw:
            return {"action": "continue"}
        cmd, _, rest = raw.partition(" ")
        if cmd == "d":
            return {"action": "drop_subtasks", "ids": rest.split(),
                    "detail": f"dropped {rest}"}
        if cmd == "g":
            return {"action": "edit_goal", "detail": rest}
        if cmd == "a":
            return {"action": "abort", "detail": rest or "no reason given"}
        return {"action": "continue"}
    if stage == "review":
        print(f"   reviewer says revise: {payload['review']['issues']}")
        print(f"   reviewer guidance: {payload['review']['revised_guidance']}")
        print("   options: [Enter]=use reviewer guidance  r <text>=replace with yours  f=force approve  e <reason>=escalate")
        raw = input("   > ").strip()
        if not raw:
            return {"action": "continue"}
        cmd, _, rest = raw.partition(" ")
        if cmd == "r":
            return {"action": "replace_guidance", "detail": rest}
        if cmd == "f":
            return {"action": "force_approve",
                    "detail": input("   why override the reviewer? (recorded): ").strip()}
        if cmd == "e":
            return {"action": "escalate", "detail": rest or "no reason given"}
        return {"action": "continue"}
    return {"action": "continue"}


def _with_guidance(sub, guidance: str):
    if guidance:
        sub = sub.model_copy(update={"instruction": f"{sub.instruction}\nReviewer guidance: {guidance}"})
    return sub


if __name__ == "__main__":
    import argparse
    from reasoning_common.config import load_variant
    ap = argparse.ArgumentParser()
    ap.add_argument("--interactive", action="store_true",
                    help="pause at plan/review boundaries (use VARIANT=steerable)")
    args = ap.parse_args()
    cfg = load_variant(PATTERN_DIR)
    sample = json.loads((PATTERN_DIR / "data" / "sample_input.json").read_text())
    ledger = CostLedger(new_run_tag(f"p03-{cfg['_variant_name']}"))
    out = run_case(sample["query"], cfg, ledger,
                   steer=select_steer(cfg, None, interactive=args.interactive))
    print(out["response"])
    print("\n--- trace ---\n" + json.dumps(out["trace"], indent=2, default=str))
    print("\n--- cost ---\n" + json.dumps(ledger.summary()["by_deployment"], indent=2))
    ledger.dump(PATTERN_DIR / "runs")
