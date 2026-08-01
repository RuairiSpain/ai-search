"""Workflow-state reasoning + HITL (§11 + §13).

The process is a governed state machine; agents occupy decision points. This
module is the state machine itself, hostable two ways:
  - mode=local   : driven in-process (used by evals; deterministic, fast)
  - mode=durable : the same transitions executed by Durable Functions
                   activities (functions_app/), which adds checkpointing,
                   week-long waits and replay-safety.

Deterministic transitions stay deterministic: the router is code, the payment
state has NO model call, and every transition is written to an audit trail as
a first-class record rather than buried in traces.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PATTERN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATTERN_DIR.parents[1] / "common"))

from reasoning_common import foundry_client as fc  # noqa: E402
from reasoning_common.budgets import Budget  # noqa: E402
from reasoning_common.config import load_budgets  # noqa: E402
from reasoning_common.costs import CostLedger  # noqa: E402
from reasoning_common.telemetry import init as telemetry_init, new_run_tag, span  # noqa: E402

STATES = ["INTAKE", "ASSESSMENT", "EXCEPTION", "PAYMENT", "CLOSED", "ESCALATED", "HOLD"]


def _instr(name: str) -> str:
    return (PATTERN_DIR / "agents" / name / "instructions.md").read_text()


def _policy() -> str:
    return (PATTERN_DIR / "skills" / "claims-policy" / "SKILL.md").read_text()


class AuditTrail:
    """§11 production control: transitions and rationales are first-class
    records, not log lines. In durable mode these persist per-case."""

    def __init__(self, case_id: str):
        self.case_id = case_id
        self.records: list[dict] = []

    def record(self, state: str, action: str, detail: dict) -> None:
        self.records.append({"ts": time.time(), "case_id": self.case_id,
                             "state": state, "action": action, "detail": detail})

    def dump(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"audit-{self.case_id}.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in self.records))
        return p


# ------------------------------------------------------------- decision points
def intake(narrative: str, cfg: dict, budget: Budget, ledger: CostLedger) -> dict:
    budget.charge()
    data, res = fc.chat_json(cfg["extractor_deployment"], [
        {"role": "system", "content": _instr("intake-extractor")},
        {"role": "user", "content": narrative},
    ], max_output_tokens=400)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, "intake")
    return data


def assess(claim: dict, cfg: dict, budget: Budget, ledger: CostLedger) -> dict:
    budget.charge()
    data, res = fc.chat_json(cfg["assessor_deployment"], [
        {"role": "system", "content": _instr("assessor") + "\n\n" + _policy()},
        {"role": "user", "content": json.dumps(claim)},
    ], max_output_tokens=500)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, "assess")
    return data


def route(claim: dict, assessment: dict, budgets_cfg: dict) -> str:
    """DETERMINISTIC router — the state machine's decision, not the model's.
    The agent recommends; this decides. No narrative can raise a limit."""
    if claim.get("missing_fields"):
        return "HOLD"
    amount = float(claim.get("amount_eur") or 0)
    covered = {"collision", "theft", "weather", "glass"}
    if claim.get("incident_type") not in covered:
        return "EXCEPTION"          # declines are human decisions here
    if claim.get("third_party_involved"):
        return "EXCEPTION"
    if amount > budgets_cfg.get("auto_approve_under_eur", 2500):
        return "EXCEPTION"
    if assessment.get("recommendation") in ("hold", "decline", "exception"):
        return "EXCEPTION"          # agent may be MORE cautious, never less
    return "PAYMENT"


def prepare_exception(claim: dict, assessment: dict, cfg: dict, budget: Budget,
                      ledger: CostLedger) -> dict:
    budget.charge()
    data, res = fc.chat_json(cfg["reviewer_deployment"], [
        {"role": "system", "content": _instr("exception-reviewer") + "\n\n" + _policy()},
        {"role": "user", "content": json.dumps({"claim": claim, "assessment": assessment})},
    ], max_output_tokens=500)
    budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
    ledger.add_result(res, "exception_package")
    return data


def pay(claim: dict) -> dict:
    """PAYMENT state: pure rules, NO model call. This is what regulators read."""
    amount = float(claim.get("amount_eur") or 0)
    return {"paid": True, "amount_eur": amount, "reference": f"PAY-{claim.get('claim_id', 'X')}",
            "executed_by": "rules-engine", "model_involved": False}


def compensate(claim: dict, reason: str) -> dict:
    """Saga rollback when a downstream step fails after payment (§11)."""
    return {"compensated": True, "reversal_ref": f"REV-{claim.get('claim_id', 'X')}",
            "reason": reason}


# ------------------------------------------------------------------ human gate
def _auto_approver(package: dict, claim: dict) -> tuple[bool, str]:
    """Scripted approver so evals stay reproducible. Approves covered-type
    claims with complete data; rejects uncovered types with a reason."""
    covered = {"collision", "theft", "weather", "glass"}
    if claim.get("incident_type") not in covered:
        return False, f"incident type {claim.get('incident_type')} is not covered under CL-4"
    return True, "reviewed: within policy, evidence sufficient"


def _cli_approver(package: dict, claim: dict) -> tuple[bool, str]:
    if not sys.stdin.isatty():
        # Safety net: a CLI approver must never block a headless run (evals,
        # CI, Durable activities). Degrade to the scripted policy and say so.
        print("WARN: no TTY — falling back to the scripted approver.", file=sys.stderr)
        return _auto_approver(package, claim)
    print(f"\n⏸  EXCEPTION REVIEW — claim {claim.get('claim_id')} "
          f"(EUR {claim.get('amount_eur')}, {claim.get('incident_type')})")
    print(f"   question: {package.get('question_for_human', '')}")
    print(f"   for:     {package.get('evidence_for', [])}")
    print(f"   against: {package.get('evidence_against', [])}")
    print(f"   if approved: {package.get('consequence_if_approved', '')}")
    ans = input("   approve? [y/N]: ").strip().lower()
    if ans == "y":
        return True, input("   note (recorded in audit trail): ").strip() or "approved"
    return False, input("   rejection reason (structured feedback, §13): ").strip() or \
        "rejected without reason (noise, not signal)"


def select_approver(cfg: dict, explicit=None, *, interactive: bool = False):
    """Choose the human gate. Injection wins; config never implies interactivity.

    Why: `run_case` is called by the shared eval runner with no approver. The
    previous version picked `_cli_approver` whenever the variant said
    approval_mode=human, so `make eval VARIANT=steerable` blocked on input()
    forever with no TTY. Interactivity is now a property of the CALL SITE
    (`--interactive`), not of the config file, so every variant is safe to
    evaluate and `make run-interactive` still works.
    """
    if explicit is not None:
        return explicit
    if interactive and cfg.get("approval_mode") == "human" and sys.stdin.isatty():
        return _cli_approver
    return _auto_approver


# --------------------------------------------------------------- state machine
def run_case(query: str, cfg: dict, ledger: CostLedger | None = None,
             approver=None) -> dict:
    ledger = ledger or CostLedger(new_run_tag("p08"))
    telemetry_init("pattern-08-workflow")
    budgets_cfg = load_budgets(PATTERN_DIR)
    budget = Budget.from_config(budgets_cfg, label="p08")

    try:
        case = json.loads(query)
        narrative = case.get("narrative", query)
    except json.JSONDecodeError:
        case, narrative = {}, query
    case_id = case.get("case_id", f"C-{int(time.time())}")
    audit = AuditTrail(case_id)
    trace: dict = {"case_id": case_id, "transitions": []}

    def transition(state: str, action: str, detail: dict):
        audit.record(state, action, detail)
        trace["transitions"].append({"state": state, "action": action})

    # --- anti-pattern variant: one agent does everything -------------------
    if cfg.get("mode") == "single_agent":
        with span("p08.single_agent"):
            budget.charge()
            res = fc.chat(cfg["assessor_deployment"], [
                {"role": "system", "content":
                 "You handle claims end to end: extract, assess, decide, and state "
                 "the payment outcome.\n\n" + _policy()},
                {"role": "user", "content": narrative},
            ], max_output_tokens=800)
            ledger.add_result(res, "single_agent")
        transition("SINGLE_AGENT", "answered", {"no_state_machine": True})
        audit.dump(PATTERN_DIR / "runs")
        return {"response": res.text,
                "trace": {**trace, "mode": "single_agent", "budget": budget.snapshot()}}

    # --- INTAKE ------------------------------------------------------------
    with span("p08.intake"):
        claim = intake(narrative, cfg, budget, ledger)
    transition("INTAKE", "extracted", {"missing_fields": claim.get("missing_fields", [])})

    if "suspicious_directive_in_narrative" in (claim.get("missing_fields") or []):
        trace["injection_flagged"] = True

    # --- ASSESSMENT --------------------------------------------------------
    with span("p08.assessment"):
        assessment = assess(claim, cfg, budget, ledger)
    transition("ASSESSMENT", "recommended", {"recommendation": assessment.get("recommendation"),
                                              "confidence": assessment.get("confidence")})

    # --- ROUTING (deterministic) -------------------------------------------
    next_state = route(claim, assessment, budgets_cfg)
    transition("ROUTER", "routed", {"to": next_state, "deterministic": True})

    if next_state == "HOLD":
        audit.dump(PATTERN_DIR / "runs")
        return {"response": (f"HOLD: claim {case_id} is incomplete — missing "
                             f"{claim.get('missing_fields')}. No payment, no decline; "
                             "the case waits for data (CL-4). Audit trail written."),
                "trace": {**trace, "final_state": "HOLD", "budget": budget.snapshot()}}

    approved, reason, package = True, "straight-through (no exception triggered)", None
    if next_state == "EXCEPTION":
        with span("p08.exception_package"):
            package = prepare_exception(claim, assessment, cfg, budget, ledger)
        transition("EXCEPTION", "package_prepared",
                   {"question": package.get("question_for_human", "")[:200]})
        fn = select_approver(cfg, approver)
        trace["approver"] = getattr(fn, "__name__", "custom")
        with budget.human_wait():   # SLA clock is business time, not budget time
            approved, reason = fn(package, claim)
        transition("EXCEPTION", "human_decision",
                   {"approved": approved, "reason": reason, "sla_hours": budgets_cfg.get("sla_hours")})
        trace["human_decision"] = {"approved": approved, "reason": reason}

    # --- PAYMENT (no LLM) or closure --------------------------------------
    if approved:
        with span("p08.payment"):
            payment = pay(claim)
        transition("PAYMENT", "executed", payment)
        transition("CLOSED", "closed", {"outcome": "paid"})
        final = (f"PAID: claim {case_id}, EUR {payment['amount_eur']} "
                 f"(ref {payment['reference']}). Executed by the rules engine — no model "
                 f"involved in the payment state. Route: "
                 f"{' -> '.join(t['state'] for t in trace['transitions'])}. "
                 f"Human decision: {reason}.")
    else:
        transition("CLOSED", "closed", {"outcome": "declined_after_review", "reason": reason})
        final = (f"DECLINED after human review: claim {case_id}. Reason: {reason}. "
                 "The structured rejection becomes evaluation data (§13). Route: "
                 f"{' -> '.join(t['state'] for t in trace['transitions'])}.")

    audit_path = audit.dump(PATTERN_DIR / "runs")
    return {"response": final,
            "trace": {**trace, "audit": str(audit_path.name),
                      "final_state": "CLOSED", "budget": budget.snapshot()}}


if __name__ == "__main__":
    import argparse
    from reasoning_common.config import load_variant
    ap = argparse.ArgumentParser()
    ap.add_argument("--interactive", action="store_true",
                    help="real human at the exception gate (VARIANT=steerable)")
    args = ap.parse_args()
    cfg = load_variant(PATTERN_DIR)
    sample = json.loads((PATTERN_DIR / "data" / "sample_input.json").read_text())
    ledger = CostLedger(new_run_tag(f"p08-{cfg['_variant_name']}"))
    out = run_case(json.dumps(sample), cfg, ledger,
                   approver=select_approver(cfg, None, interactive=args.interactive))
    print(out["response"])
    print("\n--- transitions ---\n" + json.dumps(out["trace"]["transitions"], indent=2))
    print("\n--- cost ---\n" + json.dumps(ledger.summary()["by_deployment"], indent=2))
    ledger.dump(PATTERN_DIR / "runs")
