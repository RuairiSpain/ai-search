"""The orchestrator -- replayed from history after every await point, so it
must be deterministic (docs/06-tier3-durable-agents.md §5.1). No sleep, no
datetime.utcnow(), no I/O: waiting uses `context.create_timer` and all I/O
(the actual push to the gateway) lives in the `notify` activity.

Literal copy of ../../01-durable-hello-world-status/src/orchestrations
/hello_world.py's structure -- same reasoning as every other duplicated
file across these samples, each stays independently runnable -- shortened
to three steps, 10s apart, since THIS sample's point is push notifications
delivering each status change as it happens with no client polling at all,
not narration; 30 seconds is plenty to see that land, 5 minutes would just
be slower to demo the same thing. `state`/`detail` here match
src/gateway/api/webhooks.py's ProgressPayload contract exactly, not a
simplified version of it.
"""
from __future__ import annotations

import azure.durable_functions as df

from determinism import next_step_deadline

bp = df.Blueprint()

STEP_SECONDS = 10
STEPS = [
    "warming up the greeting engine",
    "consulting the world about how it's doing",
    "wrapping up",
]


def _status_payload(task_id: str, sequence: int, detail: str, *, final: bool) -> dict:
    return {
        "task_id": task_id,
        "kind": "status",
        "sequence": sequence,
        "payload": {
            "state": "completed" if final else "working",
            "final": final,
            "detail": detail,
        },
    }


@bp.orchestration_trigger(context_name="context")
def hello_world_orchestrator(context: df.DurableOrchestrationContext):
    client_input = context.get_input()
    task_id = client_input["task_id"]

    # A plain local counter is deterministic across replay -- it's pure
    # orchestrator state, not wall-clock or random (docs/06 §5.4: "sequence
    # is assigned by the orchestrator from a replay-safe counter").
    sequence = 0

    for i, step in enumerate(STEPS, start=1):
        sequence += 1
        yield context.call_activity(
            "notify", _status_payload(task_id, sequence, f"step {i}/{len(STEPS)}: {step}", final=False)
        )
        yield context.create_timer(next_step_deadline(context, seconds=STEP_SECONDS))

    sequence += 1
    yield context.call_activity(
        "notify", _status_payload(task_id, sequence, "Hello, world!", final=True)
    )
    return "Hello, world!"
