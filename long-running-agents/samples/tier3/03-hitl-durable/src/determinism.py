"""Guarded helpers -- orchestrator code imports nothing else that touches
the clock, randomness, or I/O (docs/06-tier3-durable-agents.md §5.1).
`orchestrations/approval.py` uses only `deadline_after`, never
`datetime.utcnow()`/`time.sleep()` directly. Literal copy of
../../01-durable-hello-world-status/src/determinism.py's
`next_step_deadline`, renamed -- this sample's timer is a multi-day
approval deadline, not a fixed narration cadence, so the name should say
so, but the mechanics (and the reason a plain function suffices instead of
a class) are identical.
"""
from __future__ import annotations

from datetime import timedelta


def deadline_after(context, *, seconds: int):
    """Deterministic equivalent of `time.sleep(seconds)` inside an
    orchestrator -- context.current_utc_datetime is replay-safe,
    datetime.utcnow() is not."""
    return context.current_utc_datetime + timedelta(seconds=seconds)
