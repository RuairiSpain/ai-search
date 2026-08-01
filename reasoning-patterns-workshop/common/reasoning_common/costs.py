"""Cost accounting per deployment.

Prices default to ILLUSTRATIVE figures ($/1M tokens) that were never real
billing data — invented to demonstrate the RATIO between frontier and small
models (§18's worked example, live), not to be quoted at a customer.
`make cost`'s output says so on every run, loudly: the previous version
printed dollar figures to four decimal places with no such disclaimer, which
is precise-looking enough that an attendee could screenshot it and repeat it
as if it were real pricing (project review, item 18).

To use REAL prices instead: write a JSON file
`{"deployment_name": [input_$_per_1M, output_$_per_1M], ...}` and either pass
its path to `CostLedger(..., prices_path=...)` or set the
`REASONING_WORKSHOP_PRICES` environment variable to that path.
"""
from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from pathlib import Path

ILLUSTRATIVE_PRICES = {  # deployment name -> ($/1M input, $/1M output). MADE UP —
    # never real billing data. See module docstring for how to override.
    "frontier": (10.00, 40.00),
    "small":    (0.60, 2.40),
    "nano":     (0.10, 0.40),
    "reviewer": (1.00, 5.00),
    "router":   (0.60, 2.40),  # billed per routed model in reality; midpoint here
}


def load_prices(path: str | Path) -> dict[str, tuple[float, float]]:
    """Load REAL prices from a JSON file: {"deployment": [in_$/1M, out_$/1M]}.
    A deployment missing from the file falls back to the $1.00/$4.00 default
    used for any unrecognised deployment name (see CostLedger.add)."""
    data = json.loads(Path(path).read_text())
    return {k: tuple(v) for k, v in data.items()}


def _resolve_prices(prices_path: str | Path | None) -> tuple[dict, bool]:
    """Returns (prices_dict, is_illustrative). Checks the explicit path first,
    then the REASONING_WORKSHOP_PRICES env var, then falls back to the
    illustrative defaults — the fallback is silent-but-labelled: callers get
    `is_illustrative=True` and report() turns that into a loud banner rather
    than a print the caller has to remember to check."""
    path = prices_path or os.environ.get("REASONING_WORKSHOP_PRICES")
    if path:
        try:
            return load_prices(path), False
        except Exception as e:
            print(f"WARN: could not load prices from {path} ({type(e).__name__}: {e}); "
                  "falling back to illustrative prices.")
    return ILLUSTRATIVE_PRICES, True


class CostLedger:
    """Accumulates per-deployment usage during a run; dumped next to outputs.

    Thread-safe: patterns 03 and 05 call add_result() concurrently from a
    ThreadPoolExecutor (fan-out workers / branch expansion). list.append is
    atomic under CPython's GIL, so this was never actually corrupting data,
    but relying on GIL atomicity for correctness is exactly the habit item
    13 flags — an explicit lock makes the guarantee real rather than
    incidental, and doesn't depend on which Python build this runs under.
    """

    def __init__(self, run_tag: str, prices_path: str | Path | None = None):
        self.run_tag = run_tag
        self._rows: list[dict] = []
        self._lock = threading.Lock()
        self._prices, self.prices_illustrative = _resolve_prices(prices_path)

    def add(self, deployment: str, input_tokens: int, output_tokens: int, step: str = ""):
        pi, po = self._prices.get(deployment, (1.0, 4.0))
        usd = input_tokens / 1e6 * pi + output_tokens / 1e6 * po
        row = dict(deployment=deployment, step=step,
                  input_tokens=input_tokens, output_tokens=output_tokens,
                  usd=round(usd, 6))
        with self._lock:
            self._rows.append(row)

    def add_result(self, res, step: str = ""):
        self.add(res.model_deployment, res.input_tokens, res.output_tokens, step)

    def summary(self) -> dict:
        with self._lock:
            rows = list(self._rows)  # snapshot: don't hold the lock during computation
        by = defaultdict(lambda: dict(calls=0, input_tokens=0, output_tokens=0, usd=0.0))
        for r in rows:
            b = by[r["deployment"]]
            b["calls"] += 1
            b["input_tokens"] += r["input_tokens"]
            b["output_tokens"] += r["output_tokens"]
            b["usd"] = round(b["usd"] + r["usd"], 6)
        return {"run_tag": self.run_tag,
                "total_usd": round(sum(r["usd"] for r in rows), 6),
                "by_deployment": dict(by), "rows": rows,
                "prices_illustrative": self.prices_illustrative}

    def dump(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"cost-{self.run_tag}.json"
        p.write_text(json.dumps(self.summary(), indent=2))
        return p


def report(runs_dir: Path) -> str:
    """Aggregate all cost-*.json files into the `make cost` table.

    Older cost-*.json files written before this fix have no
    `prices_illustrative` key — those are treated as illustrative (the only
    thing this repo ever produced before real-price support existed), so the
    banner still fires rather than silently under-warning on stale files.
    """
    table = [f"{'run':32} {'total $':>10}  prices        breakdown"]
    any_illustrative = False
    for p in sorted(runs_dir.glob("cost-*.json")):
        s = json.loads(p.read_text())
        illustrative = s.get("prices_illustrative", True)
        any_illustrative = any_illustrative or illustrative
        marker = "ILLUSTRATIVE" if illustrative else "real"
        parts = ", ".join(f"{d}: ${v['usd']:.4f}/{v['calls']}c"
                          for d, v in s["by_deployment"].items())
        table.append(f"{s['run_tag']:32} {s['total_usd']:>10.4f}  {marker:12}  {parts}")

    if len(table) == 1:
        return "No runs yet — `make run` first."

    if any_illustrative:
        banner = ("⚠ AT LEAST ONE ROW USES ILLUSTRATIVE PRICES — invented for this workshop,\n"
                  "  NEVER real billing data. Do not quote these dollar figures to a customer;\n"
                  "  the ratio between deployments is the point, not the absolute numbers.\n"
                  "  To use real prices: set REASONING_WORKSHOP_PRICES to a JSON file of\n"
                  "  {\"deployment\": [input_$/1M, output_$/1M]} — see costs.py docstring.\n")
        return banner + "\n".join(table)
    return "\n".join(table)
