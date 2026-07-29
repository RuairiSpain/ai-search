"""The orchestrator -- replayed from history after every await point, so it
must be deterministic (docs/06-tier3-durable-agents.md §5.1). No sleep, no
datetime.utcnow(), no I/O: waiting uses `context.create_timer` and all I/O
(the actual push to the gateway) lives in the `notify` activity.

Five narrated steps, 60s apart -- 5 minutes total, same wall-clock length
as ../../../tier2/04-long-running-hello-world's silent sleep, but every
step pushes a `gw.progress.v1` status event via `notify` before waiting for
the next one. `state`/`detail` here match
src/gateway/api/webhooks.py's ProgressPayload contract exactly, not a
simplified version of it.
"""
from __future__ import annotations

import azure.durable_functions as df

from determinism import next_step_deadline

bp = df.Blueprint()

STEP_SECONDS = 60
STEPS = [
    "warming up the greeting engine",
    "consulting the world about how it's doing",
    "double-checking punctuation",
    "polishing the exclamation mark",
    "wrapping up",
]


def _trace_id_from(traceparent: str | None) -> str | None:
    """W3C traceparent shape is `version-traceid-parentid-flags`
    (docs/05 §6.3, docs/06 §6.3) -- only the trace-id segment is worth
    carrying through every notify() payload; a full parser/validator lives
    in the gateway's own `gateway.tracing` module, not duplicated here
    (this sample stays independently deployable, no dependency on this
    repo's own gateway package). A plain string extraction, called once
    per orchestration run from client_input -- no clock, no randomness, no
    I/O, so this stays replay-safe (docs/06 §5.1) without needing to live
    in determinism.py alongside the timer helper."""
    if not traceparent:
        return None
    parts = traceparent.split("-")
    return parts[1] if len(parts) == 4 else None


def _status_payload(
    task_id: str, sequence: int, detail: str, *, final: bool, trace_id: str | None
) -> dict:
    payload = {
        "task_id": task_id,
        "kind": "status",
        "sequence": sequence,
        "payload": {
            "state": "completed" if final else "working",
            "final": final,
            "detail": detail,
        },
    }
    if trace_id:
        # Not part of ProgressPayload's own contract -- webhooks.py stores
        # this dict as opaque JSONB and gw_event's own reader only ever
        # looks at state/detail/final, so an extra key here is inert to
        # the gateway's own processing. Purely for log correlation: an
        # operator grepping this sample's own logs for a stuck task can
        # match the same trace-id the gateway logged for the SendMessage
        # that started it.
        payload["payload"]["trace_id"] = trace_id
    return payload


@bp.orchestration_trigger(context_name="context")
def hello_world_orchestrator(context: df.DurableOrchestrationContext):
    client_input = context.get_input()
    task_id = client_input["task_id"]
    trace_id = _trace_id_from(client_input.get("traceparent"))

    # A plain local counter is deterministic across replay -- it's pure
    # orchestrator state, not wall-clock or random (docs/06 §5.4: "sequence
    # is assigned by the orchestrator from a replay-safe counter").
    sequence = 0

    for i, step in enumerate(STEPS, start=1):
        sequence += 1
        yield context.call_activity(
            "notify",
            _status_payload(
                task_id, sequence, f"step {i}/{len(STEPS)}: {step}", final=False, trace_id=trace_id
            ),
        )
        yield context.create_timer(next_step_deadline(context, seconds=STEP_SECONDS))

    sequence += 1
    yield context.call_activity(
        "notify", _status_payload(task_id, sequence, "Hello, world!", final=True, trace_id=trace_id)
    )
    return "Hello, world!"
