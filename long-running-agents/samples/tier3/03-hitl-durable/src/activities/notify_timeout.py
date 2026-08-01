"""Logged, not pushed anywhere real -- same honesty caveat as
request_approval.py: no real Teams integration in this sample.
"""
from __future__ import annotations

import logging

from orchestrations.approval import bp

log = logging.getLogger(__name__)


@bp.activity_trigger(input_name="payload")
async def notify_timeout(payload: dict) -> None:
    log.warning("expense approval for task %s expired unapproved: %r", payload["task_id"], payload["expense"])
