#!/usr/bin/env python3
"""CI-suitable smoke test: drives every pattern's `run_case()` end-to-end
using the fake backend (`reasoning_common.fake_backend`) — no live Azure
endpoint, no MCP network dependency, no cost. Runs in a few seconds.

This is the answer to project review item 20: `scripts/verify_offline.py`
proves the pure logic (budgets, evaluators, contracts, sandbox) is correct in
isolation; this proves each pattern's ACTUAL `run_case()` executes without
crashing against its own sample input — the gap between "the modules import"
and "the patterns work". It is not a substitute for real evaluation against
real models (the fake backend returns structurally valid, not necessarily
GOOD, responses) — it's a fast, free, offline correctness gate for the
control-flow, contracts, and Azure-adjacent glue every pattern depends on.

    python3 scripts/run_ci_smoke.py            # all 11 patterns
    python3 scripts/run_ci_smoke.py 03 05       # just these two (by number)

Exit code 0 iff every pattern's run_case() returned a well-formed dict
without raising (an "__ERROR__" response counts as a caught, non-crashing
failure — a bare exception does not).
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))

from reasoning_common import fake_backend  # noqa: E402
from reasoning_common.config import load_variant  # noqa: E402
from reasoning_common.costs import CostLedger  # noqa: E402

PATTERNS = [
    "01-deliberate-reasoning", "02-react-tool-loop", "03-multi-agent-routing",
    "04-neuro-symbolic", "05-branching-hypotheses", "06-memory-augmented",
    "07-reflection-skills", "08-workflow-state-hitl", "09-search-exploration",
    "10-graph-reasoning", "11-program-synthesis",
]

# Every pattern's baseline variant with a sample query is enough to exercise
# the full control loop once. Some need a non-default variant or a JSON
# query payload; keep that per-pattern here rather than special-casing the
# driver loop below.
VARIANT_OVERRIDE = {"11-program-synthesis": "baseline"}


def _pattern_dir(name: str) -> Path:
    return ROOT / "patterns" / name


def _sample_query(pdir: Path) -> str:
    raw = json.loads((pdir / "data" / "sample_input.json").read_text())
    # patterns 06/07/08 pass a JSON-encoded case through `query`; others use
    # a bare "query" field. Try both shapes so this stays generic.
    if "query" in raw and len(raw) == 1:
        return raw["query"]
    return json.dumps(raw)


def run_one(name: str) -> dict:
    pdir = _pattern_dir(name)
    src = pdir / "src"
    sys.path.insert(0, str(src))
    for mod in ("workflow",):
        sys.modules.pop(mod, None)
    t0 = time.monotonic()
    result = {"pattern": name, "ok": False, "elapsed_s": 0.0, "detail": ""}
    try:
        workflow = importlib.import_module("workflow")
        fake_backend.install_into(workflow)  # direct-imported call_mcp_tool/shield fns
        variant = VARIANT_OVERRIDE.get(name, "baseline")
        cfg = load_variant(pdir, variant)
        query = _sample_query(pdir)
        ledger = CostLedger(f"ci-smoke-{name}")
        out = workflow.run_case(query, cfg, ledger)
        if not isinstance(out, dict) or "response" not in out or "trace" not in out:
            raise AssertionError(f"run_case returned malformed shape: {out!r}")
        result["ok"] = True
        result["detail"] = out["response"][:120]
    except Exception as e:  # noqa: BLE001 — a smoke test must catch everything
        result["detail"] = f"{type(e).__name__}: {e}"
    finally:
        result["elapsed_s"] = round(time.monotonic() - t0, 2)
        sys.path.remove(str(src))
    return result


def main() -> int:
    fake_backend.install()
    requested = sys.argv[1:]
    names = [p for p in PATTERNS if not requested or p.split("-")[0] in requested]

    print(f"{'pattern':32} {'status':6} {'time':>6}  detail")
    print("-" * 100)
    failures = []
    for name in names:
        r = run_one(name)
        status = "OK" if r["ok"] else "FAIL"
        if not r["ok"]:
            failures.append(name)
        print(f"{name:32} {status:6} {r['elapsed_s']:>5.2f}s  {r['detail']}")

    print("-" * 100)
    if failures:
        print(f"{len(failures)}/{len(names)} pattern(s) FAILED: {failures}")
        return 1
    print(f"All {len(names)} pattern(s) executed run_case() end-to-end without crashing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
