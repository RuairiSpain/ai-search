#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
install_shared_reqs pytest==9.1.1

echo "==> Sanity: the deterministic close evaluator rejects the legacy skill set on zeta"
python3 - << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("../..").resolve() / "common")); sys.path.insert(0, "src")
from workflow import evaluate_close
zeta = Path("fixture/subsidiary_zeta.csv")
ok, why = evaluate_close(zeta, {"format_recognized": False, "totals": {}})
assert not ok, "evaluator should fail an unrecognised-format run"
ok2, _ = evaluate_close(zeta, {"format_recognized": True, "reconciled": False,
                               "totals": {"revenue": 95000, "cost_of_sales": -60000,
                                          "opex": -20000, "tax": -3000}})
assert ok2, "evaluator should accept correct zeta totals"
print("  close evaluator OK (fails unparsed, passes correct totals)")
PY
echo "✅ pattern 07 ready. Try: make run"
