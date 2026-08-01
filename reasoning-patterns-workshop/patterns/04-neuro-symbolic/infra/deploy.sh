#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
install_shared_reqs

echo "==> Verifying the rules engine answers deterministically over MCP"
python3 - << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("../..").resolve() / "common"))
from reasoning_common.mcp_client import call_mcp_tool
case = {"risk_score": 80, "pep": True, "exposure_eur": 5000, "jurisdiction": "PT", "id_verified": True}
a = call_mcp_tool("evaluate_rules", {"case": case})
b = call_mcp_tool("evaluate_rules", {"case": case})
assert a == b, "rules engine is not deterministic?!"
ids = [r["id"] for r in a["triggered_rules"]]
assert "KYC-001" in ids and "KYC-014" in ids, f"unexpected verdict: {a}"
print(f"  rules engine OK — triggered {ids}, permitted={a['permitted']}")
PY
echo "✅ pattern 04 ready. Try: make run"
