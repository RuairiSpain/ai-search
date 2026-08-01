#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
install_shared_reqs

echo "==> Verifying migration catalog tools on the MCP server"
python3 - << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("../..").resolve() / "common"))
from reasoning_common.mcp_client import call_mcp_tool
cat = call_mcp_tool("get_system_catalog", {})
assert "S1" in cat and "S8" in cat, f"catalog incomplete: {list(cat)[:3]}... — re-run infra/shared/deploy.sh to rebuild the MCP image with phase-3 routes"
print(f"  catalog OK: {len(cat)} systems")
PY
echo "✅ pattern 09 ready. Try: make run"
