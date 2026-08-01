#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
install_shared_reqs

echo "==> MCP server health check: $MCP_SERVER_URL/health"
curl -fsS "$MCP_SERVER_URL/health" >/dev/null || {
  echo "MCP server unreachable — re-run infra/shared/deploy.sh"; exit 1; }

echo "==> Registering agents for every variant (idempotent)"
python3 - << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("../..").resolve() / "common"))
sys.path.insert(0, "src")
from reasoning_common.config import load_variant
import workflow
for v in sorted(p.stem for p in Path("variants").glob("*.yaml")):
    cfg = load_variant(Path("."), v)
    aid = workflow.ensure_agent(cfg)
    print(f"  {cfg['agent_name']:36} -> {aid}")
PY
echo "✅ pattern 02 deployed. Try: make run   then open the thread in the Activity tab."
