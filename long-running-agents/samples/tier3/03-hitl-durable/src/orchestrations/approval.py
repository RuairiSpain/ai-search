"""Multi-day human-in-the-loop expense approval -- a runnable version of
docs/06-tier3-durable-agents.md §5.3's own snippet. Deterministic (§5.1):
no sleep, no datetime.utcnow(); `timeout_seconds` arrives via
`client_input` rather than being hardcoded, so this sample's own
deliberate failure path (a short override) doesn't need a second
orchestrator function -- see ../../README.md.

Two rules from docs/06 §5.3, followed exactly: **always cancel the losing
timer** (an uncancelled durable timer keeps the instance alive), and
**give every wait a deadline** (a wait with no deadline that never
resolves is invisible until someone audits instance counts). Only the
timer needs an explicit cancel when approval wins -- there's no operation
to cancel an unresolved `wait_for_external_event`; when the deadline wins
instead, a late-arriving APPROVAL event against an already-completed
instance is a documented no-op on the Durable Functions side, not
something this orchestrator has to guard against itself.
"""
from __future__ import annotations

import azure.durable_functions as df

from determinism import deadline_after

bp = df.Blueprint()

DEFAULT_TIMEOUT_SECONDS = 14 * 24 * 3600  # 14 days -- docs/06 §5.3's own example


def _status_payload(task_id: str, sequence: int, *, state: str, detail: str, final: bool) -> dict:
    return {
        "task_id": task_id,
        "kind": "status",
        "sequence": sequence,
        "payload": {"state": state, "final": final, "detail": detail},
    }


@bp.orchestration_trigger(context_name="context")
def expense_approval_orchestrator(context: df.DurableOrchestrationContext):
    client_input = context.get_input()
    task_id = client_input["task_id"]
    expense = client_input["text"]
    timeout_seconds = client_input.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)

    sequence = 0

    yield context.call_activity("request_approval", {"task_id": task_id, "expense": expense})

    # Pushed BEFORE the wait, not after -- a client polling (or, in
    # ../../05-push-notifications, subscribed for a push) needs to see
    # `input-required` the moment the orchestration actually starts
    # waiting, not find out only once someone eventually answers.
    sequence += 1
    yield context.call_activity(
        "notify",
        _status_payload(
            task_id, sequence, state="input-required", detail=f"Waiting for approval: {expense}", final=False
        ),
    )

    approval = context.wait_for_external_event("APPROVAL")
    deadline = context.create_timer(deadline_after(context, seconds=timeout_seconds))
    winner = yield context.task_any([approval, deadline])

    sequence += 1
    if winner == approval:
        deadline.cancel()  # ALWAYS cancel the loser (docs/06 §5.3)
        decision = approval.result
        if decision.get("decision") == "approved":
            result = yield context.call_activity(
                "reimburse", {"task_id": task_id, "expense": expense, **decision}
            )
            yield context.call_activity(
                "notify", _status_payload(task_id, sequence, state="completed", detail=result, final=True)
            )
            return result

        reason = decision.get("reason", "rejected")
        yield context.call_activity(
            "notify", _status_payload(task_id, sequence, state="rejected", detail=reason, final=True)
        )
        return {"status": "rejected", "reason": reason}

    # Deadline won -- the deliberate failure path this sample's README
    # walks through explicitly, not just the happy approval path (every
    # sample in this repo must show at least one, per samples/README.md).
    yield context.call_activity("notify_timeout", {"task_id": task_id, "expense": expense})
    yield context.call_activity(
        "notify",
        _status_payload(
            task_id, sequence, state="failed", detail="expense approval request expired unapproved", final=True
        ),
    )
    return {"status": "expired"}
