"""Durable Functions host for the SAME state machine (§11).

Deliberate discipline, and the thing to point at in the workshop:
  * the ORCHESTRATOR is deterministic — no model calls, no I/O, no datetime.now;
    it only sequences activities and waits. Replay-safety depends on this.
  * every side effect (model calls, payment, audit writes) lives in an ACTIVITY.
  * the human gate is wait_for_external_event + a durable TIMER — the SLA
    escalation path §13 asks for, surviving process restarts and week-long waits.

Local run:  cd functions_app && func start
Deploy:     ../infra/deploy-durable.sh
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import azure.durable_functions as df
import azure.functions as func

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "common"))

app = df.DFApp(http_auth_level=func.AuthLevel.FUNCTION)


# ------------------------------------------------------------------ HTTP start
@app.route(route="claims")
@app.durable_client_input(client_name="client")
async def start_claim(req: func.HttpRequest, client) -> func.HttpResponse:
    payload = req.get_json()
    instance_id = await client.start_new("claim_orchestrator", None, payload)
    return client.create_check_status_response(req, instance_id)


# --------------------------------------------------------------- approval POST
@app.route(route="claims/{instance_id}/approval")
@app.durable_client_input(client_name="client")
async def post_approval(req: func.HttpRequest, client) -> func.HttpResponse:
    instance_id = req.route_params.get("instance_id")
    body = req.get_json()  # {"approved": bool, "reason": str}
    await client.raise_event(instance_id, "ApprovalDecision", body)
    return func.HttpResponse("decision recorded", status_code=202)


# ------------------------------------------------------------- orchestrator
@app.orchestration_trigger(context_name="context")
def claim_orchestrator(context: df.DurableOrchestrationContext):
    """DETERMINISTIC: sequencing and waiting only."""
    payload = context.get_input()
    claim = yield context.call_activity("act_intake", payload)
    assessment = yield context.call_activity("act_assess", claim)
    next_state = yield context.call_activity("act_route", {"claim": claim,
                                                            "assessment": assessment})
    if next_state == "HOLD":
        yield context.call_activity("act_audit", {"state": "HOLD", "claim": claim})
        return {"outcome": "hold", "claim": claim}

    approved, reason = True, "straight-through"
    if next_state == "EXCEPTION":
        package = yield context.call_activity("act_exception_package",
                                              {"claim": claim, "assessment": assessment})
        yield context.call_activity("act_notify_reviewer",
                                    {"package": package, "instance_id": context.instance_id})

        # Human interaction pattern + SLA timer (§13): whichever lands first.
        approval_event = context.wait_for_external_event("ApprovalDecision")
        deadline = context.current_utc_datetime + timedelta(hours=24)
        timer = context.create_timer(deadline)
        winner = yield context.task_any([approval_event, timer])
        if winner == approval_event:
            timer.cancel()
            decision = approval_event.result
            approved = bool(decision.get("approved"))
            reason = decision.get("reason", "")
        else:
            yield context.call_activity("act_escalate",
                                        {"claim": claim, "reason": "SLA breach: no decision in 24h"})
            return {"outcome": "escalated", "claim": claim}

    if approved:
        payment = yield context.call_activity("act_pay", claim)
        try:
            yield context.call_activity("act_notify_finance", payment)
        except Exception as e:
            # Saga compensation (§11): undo the payment an agent-triggered
            # path already executed when a downstream step fails. The real
            # exception is passed through to the audit record rather than
            # collapsed into a fixed string — a broad except that always
            # says "finance notification failed" hides what actually broke
            # (auth error, quota, a genuine bug) from whoever reads the
            # audit trail afterward.
            yield context.call_activity("act_compensate",
                                        {"claim": claim,
                                         "reason": f"downstream failure: {type(e).__name__}: {e}"})
            return {"outcome": "compensated", "claim": claim}
        yield context.call_activity("act_audit", {"state": "PAID", "claim": claim,
                                                   "payment": payment, "reason": reason})
        return {"outcome": "paid", "payment": payment}

    yield context.call_activity("act_audit", {"state": "DECLINED", "claim": claim,
                                               "reason": reason})
    return {"outcome": "declined", "reason": reason}


# ------------------------------------------------------------------ activities
# All side effects live here. Activities must be IDEMPOTENT: Durable replays.
def _cfg():
    from reasoning_common.config import load_variant
    return load_variant(Path(__file__).resolve().parents[1], "baseline")


def _ledger():
    from reasoning_common.costs import CostLedger
    from reasoning_common.telemetry import new_run_tag
    return CostLedger(new_run_tag("p08-durable"))


@app.activity_trigger(input_name="payload")
def act_intake(payload: dict) -> dict:
    import workflow as wf
    from reasoning_common.budgets import Budget
    return wf.intake(payload.get("narrative", ""), _cfg(), Budget(label="durable"), _ledger())


@app.activity_trigger(input_name="claim")
def act_assess(claim: dict) -> dict:
    import workflow as wf
    from reasoning_common.budgets import Budget
    return wf.assess(claim, _cfg(), Budget(label="durable"), _ledger())


@app.activity_trigger(input_name="payload")
def act_route(payload: dict) -> str:
    import workflow as wf
    from reasoning_common.config import load_budgets
    return wf.route(payload["claim"], payload["assessment"],
                    load_budgets(Path(__file__).resolve().parents[1]))


@app.activity_trigger(input_name="payload")
def act_exception_package(payload: dict) -> dict:
    import workflow as wf
    from reasoning_common.budgets import Budget
    return wf.prepare_exception(payload["claim"], payload["assessment"], _cfg(),
                                Budget(label="durable"), _ledger())


@app.activity_trigger(input_name="payload")
def act_notify_reviewer(payload: dict) -> str:
    # Production: Teams adaptive card / Logic Apps connector posting to where
    # reviewers already work, with the approval URL for this instance.
    print(f"[REVIEWER NOTIFICATION] instance {payload['instance_id']}: "
          f"{json.dumps(payload['package'])[:400]}")
    return "notified"


@app.activity_trigger(input_name="claim")
def act_pay(claim: dict) -> dict:
    import workflow as wf
    return wf.pay(claim)   # no LLM in the payment state


@app.activity_trigger(input_name="payment")
def act_notify_finance(payment: dict) -> str:
    return "finance notified"


@app.activity_trigger(input_name="payload")
def act_compensate(payload: dict) -> dict:
    import workflow as wf
    return wf.compensate(payload["claim"], payload["reason"])


@app.activity_trigger(input_name="payload")
def act_escalate(payload: dict) -> str:
    print(f"[ESCALATION] {payload['reason']} for claim {payload['claim'].get('claim_id')}")
    return "escalated"


@app.activity_trigger(input_name="payload")
def act_audit(payload: dict) -> str:
    print(f"[AUDIT] {json.dumps(payload)[:600]}")
    return "recorded"
