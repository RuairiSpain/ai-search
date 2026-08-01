"""Shared eval runner: every pattern's `make eval` funnels through here.

Flow: run the pattern's target function over data/eval_dataset.jsonl locally
(capturing outputs + cost), write an enriched JSONL, upload it as a versioned
dataset, then submit a cloud evaluation with the pattern's evaluator config.
The run is tagged with pattern + variant + git-ish stamp so two runs are
comparable side-by-side in the portal's Experiments table.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

from . import foundry_client as fc
from .config import load_variant
from .costs import CostLedger


def run(pattern_dir: Path, variant_name: str | None, limit: int | None) -> None:
    pattern_dir = pattern_dir.resolve()
    sys.path.insert(0, str(pattern_dir / "src"))
    cfg = load_variant(pattern_dir, variant_name)
    variant = cfg["_variant_name"]
    pattern = cfg["_pattern"]
    stamp = time.strftime("%m%d-%H%M%S")
    run_name = f"{pattern}-{variant}-{stamp}"

    # 1. Local pass: produce responses for every eval row --------------------
    target = importlib.import_module("workflow")  # each pattern exposes run_case()
    rows_in = [json.loads(l) for l in
               (pattern_dir / "data" / "eval_dataset.jsonl").read_text().splitlines() if l.strip()]
    if limit:
        rows_in = rows_in[:limit]

    ledger = CostLedger(run_name)
    enriched = []
    for i, row in enumerate(rows_in, 1):
        print(f"[{i}/{len(rows_in)}] {row.get('id', '?')} ...", flush=True)
        try:
            out = target.run_case(row["query"], cfg, ledger)
        except Exception as e:  # a failing case is DATA, not a crash
            out = {"response": f"__ERROR__ {type(e).__name__}: {e}", "trace": {}}
        enriched.append({**row,
                         "response": out["response"],
                         "tool_calls": json.dumps(out.get("trace", {}))})

    out_path = pattern_dir / "runs" / f"{run_name}.jsonl"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text("\n".join(json.dumps(r) for r in enriched))
    cost_file = ledger.dump(pattern_dir / "runs")
    print(f"Responses -> {out_path}\nCost      -> {cost_file}")
    print(json.dumps(ledger.summary()["by_deployment"], indent=2))

    # 2. Cloud evaluation (OpenAI evals API on the project, SDK-verified) ----
    evaluators_mod = importlib.import_module("evaluators")  # pattern's evals/ on path
    eval_id, run_id = fc.run_cloud_evaluation(
        display_name=run_name,
        rows=enriched,
        testing_criteria=evaluators_mod.TESTING_CRITERIA,
        tags={"pattern": pattern, "variant": variant},
    )
    print(f"\nEvaluation submitted: eval={eval_id} run={run_id}")
    print("Portal: your project -> Evaluations. Compare runs with different variants there.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run pattern evaluation")
    ap.add_argument("--pattern-dir", type=Path, default=Path.cwd())
    ap.add_argument("--variant", default=None)
    ap.add_argument("--limit", type=int, default=None, help="Only first N rows (smoke test)")
    a = ap.parse_args()
    sys.path.insert(0, str(a.pattern_dir / "evals"))
    run(a.pattern_dir, a.variant, a.limit)


if __name__ == "__main__":
    main()
