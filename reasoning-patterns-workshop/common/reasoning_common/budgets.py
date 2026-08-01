"""Reasoning budgets — §18 made executable.

Every loop in every pattern runs inside a Budget. When a limit trips, the loop
stops and (depending on the pattern) escalates instead of spending more. The
breach shows up in traces and in `make cost` output — that's the teaching point.
"""
from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    """Raised when a loop hits its reasoning budget. Catch it to escalate."""


@dataclass
class Budget:
    max_llm_calls: int = 12
    max_total_tokens: int = 60_000
    max_wall_clock_s: float = 120.0
    label: str = "default"

    _calls: int = field(default=0, init=False)
    _tokens: int = field(default=0, init=False)
    _t0: float = field(default_factory=time.monotonic, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _human_wait_s: float = field(default=0.0, init=False)

    def charge(self, *, calls: int = 1, tokens: int = 0) -> None:
        # Thread-safe: pattern 03 charges from parallel fan-out workers.
        # Mutation AND the threshold checks share one lock — the previous
        # version incremented under the lock but checked afterward, which
        # was logically safe only by reasoning about CPython's GIL (atomic
        # int/float reads, monotonic counters that never decrease). Correct
        # by construction is better than correct by GIL: doing both under
        # one lock costs nothing here (the checks are cheap) and doesn't
        # depend on which Python implementation runs it.
        with self._lock:
            self._calls += calls
            self._tokens += tokens
            if self._calls > self.max_llm_calls:
                raise BudgetExceeded(f"[{self.label}] > {self.max_llm_calls} LLM calls")
            if self._tokens > self.max_total_tokens:
                raise BudgetExceeded(f"[{self.label}] > {self.max_total_tokens} tokens")
            elapsed = time.monotonic() - self._t0 - self._human_wait_s
            if elapsed > self.max_wall_clock_s:
                raise BudgetExceeded(f"[{self.label}] > {self.max_wall_clock_s}s wall clock")

    @contextlib.contextmanager
    def human_wait(self):
        """Steering pause: the wall clock stops while a human is thinking —
        their deliberation is the point, not a cost to punish. Calls and
        tokens keep counting."""
        t0 = time.monotonic()
        try:
            yield
        finally:
            with self._lock:
                self._human_wait_s += time.monotonic() - t0

    def snapshot(self) -> dict:
        return {
            "label": self.label,
            "llm_calls": self._calls,
            "tokens": self._tokens,
            "elapsed_s": round(time.monotonic() - self._t0, 2),
            "human_wait_s": round(self._human_wait_s, 2),
        }

    @classmethod
    def from_config(cls, cfg: dict, label: str) -> "Budget":
        return cls(
            max_llm_calls=cfg.get("max_llm_calls", 12),
            max_total_tokens=cfg.get("max_total_tokens", 60_000),
            max_wall_clock_s=cfg.get("max_wall_clock_s", 120),
            label=label,
        )
