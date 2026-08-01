"""Agent Optimizer exercise (§10/§17 'close the loop').

Preferred path: Foundry Agent Optimizer in the portal (project -> Agents ->
p01-deliberate-playground -> Optimize) runs evaluate-generate-rank-deploy over
the agent definition using your eval dataset.

This script is the FALLBACK manual loop for tenants where Optimizer is still
preview-gated — and it's also the transparent version of what Optimizer does:
  1. run eval on current instructions   2. feed failures to a frontier model
  3. get rewritten instructions         4. write instructions.v2.md
  5. you re-run: make eval VARIANT=improved-instructions   and compare.
Governance gate (§10): the rewrite lands in a FILE for you to diff-review and
commit — it is never auto-deployed.
"""
import json
import sys
from pathlib import Path

PATTERN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATTERN_DIR.parents[1] / "common"))
sys.path.insert(0, str(PATTERN_DIR / "src"))

from reasoning_common import foundry_client as fc
from reasoning_common.config import load_variant
from reasoning_common.costs import CostLedger
from reasoning_common.text_utils import strip_code_fences
import workflow

def main() -> None:
    cfg = load_variant(PATTERN_DIR, "baseline")
    rows = [json.loads(l) for l in (PATTERN_DIR / "data/eval_dataset.jsonl").read_text().splitlines()][:5]
    ledger = CostLedger("p01-optimize")

    print("Step 1/3: running current instructions over 5 eval rows...")
    transcripts = []
    for r in rows:
        out = workflow.run_case(r["query"], cfg, ledger)
        transcripts.append({"query": r["query"], "ground_truth": r["ground_truth"],
                            "response": out["response"][:1500]})

    print("Step 2/3: asking frontier model to rewrite instructions from failures...")
    current = (PATTERN_DIR / cfg["instructions_file"]).read_text()
    res = fc.chat("frontier", [
        {"role": "system", "content":
         "You improve agent instruction files. Given current instructions and eval "
         "transcripts with ground truths, rewrite the instructions to fix observed "
         "gaps. Keep the JSON output contract IDENTICAL. Keep it under 60 lines. "
         "Return ONLY the new markdown."},
        {"role": "user", "content": f"CURRENT INSTRUCTIONS:\n{current}\n\nTRANSCRIPTS:\n{json.dumps(transcripts, indent=2)}"},
    ], max_output_tokens=1500)
    ledger.add_result(res, "rewrite")

    new_md = strip_code_fences(res.text)
    out_path = PATTERN_DIR / "agents/deliberate-diagnostician/instructions.v2.md"
    out_path.write_text(new_md + "\n")
    print(f"Step 3/3: wrote {out_path}\n")
    print("Review the diff, then:  make eval VARIANT=improved-instructions")
    print("Compare both runs in portal -> Evaluations -> select rows -> Compare.")


if __name__ == "__main__":
    main()
