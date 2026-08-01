"""Guarded helpers -- orchestrator code imports nothing else that touches
the clock, randomness, or I/O (docs/06-tier3-durable-agents.md §5.1).
`orchestrations/hello_world.py` uses only `next_step_deadline`, never
`datetime.utcnow()`/`time.sleep()` directly.
"""
from __future__ import annotations

from datetime import timedelta


def next_step_deadline(context, *, seconds: int):
    """Deterministic equivalent of `time.sleep(seconds)` inside an
    orchestrator -- context.current_utc_datetime is replay-safe,
    datetime.utcnow() is not."""
    return context.current_utc_datetime + timedelta(seconds=seconds)
