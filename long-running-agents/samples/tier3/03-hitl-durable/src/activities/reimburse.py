"""The "do the thing" step once a human approves. Deliberately trivial --
this sample is about the HITL pause/resume mechanics (wait_for_external_event,
the gateway's `input-required` mapping, `client.raise_event`), not payment
processing, so there's nothing here to distract from that.
"""
from __future__ import annotations

from orchestrations.approval import bp


@bp.activity_trigger(input_name="payload")
async def reimburse(payload: dict) -> str:
    return f"Reimbursed {payload['expense']!r} (approved by {payload.get('approved_by', 'unknown')})."
