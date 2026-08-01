#!/usr/bin/env bash
# Local mode (default): no Azure resources needed beyond shared infra.
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
install_shared_reqs

echo "==> Sanity: the deterministic router honours CL-4 thresholds"
python3 - << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("../..").resolve() / "common")); sys.path.insert(0, "src")
from workflow import route
b = {"auto_approve_under_eur": 2500}
assert route({"amount_eur": 180, "incident_type": "glass"}, {"recommendation": "pay"}, b) == "PAYMENT"
assert route({"amount_eur": 7400, "incident_type": "collision"}, {"recommendation": "pay"}, b) == "EXCEPTION"
assert route({"amount_eur": 100, "incident_type": "collision", "third_party_involved": True}, {"recommendation": "pay"}, b) == "EXCEPTION"
assert route({"amount_eur": 100, "incident_type": "wear_and_tear"}, {"recommendation": "pay"}, b) == "EXCEPTION"
assert route({"missing_fields": ["amount_eur"]}, {"recommendation": "pay"}, b) == "HOLD"
assert route({"amount_eur": 100, "incident_type": "glass"}, {"recommendation": "hold"}, b) == "EXCEPTION"
print("  router OK: thresholds, third-party, coverage, completeness, agent-caution all enforced")
PY
echo "✅ pattern 08 ready (local mode). Try: make run   |   durable mode: ./infra/deploy-durable.sh"
