"""Stands in for docs/06 §5.3's own `request_approval` snippet ("posts to
Teams"). ⚠ This sample does NOT implement a real Teams `conversationReference`
proactive-message flow (docs/06 §4.3, and the §7 "Before shipping a
multi-day HITL app" checklist item "Teams conversationReference stored and
proactive delivery tested" -- explicitly left undone here, see
../../README.md). Standing in for it: log the exact `client/approve.py`
invocation a human needs to run, since this sample's own client script IS
its approval channel.
"""
from __future__ import annotations

import logging

from orchestrations.approval import bp

log = logging.getLogger(__name__)


@bp.activity_trigger(input_name="payload")
async def request_approval(payload: dict) -> None:
    log.warning(
        "APPROVAL NEEDED for task %s: %r -- run: python client/approve.py %s --decision approved",
        payload["task_id"],
        payload["expense"],
        payload["task_id"],
    )
