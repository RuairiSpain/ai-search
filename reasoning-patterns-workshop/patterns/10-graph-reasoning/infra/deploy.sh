#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
install_shared_reqs

echo "==> Verifying graph tools + seeded ring on the MCP server"
python3 - << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("../..").resolve() / "common"))
from reasoning_common.mcp_client import call_mcp_tool
n = call_mcp_tool("get_neighbors", {"entity_id": "D2", "relation": "uses_device"})
ids = sorted(x["entity_id"] for x in n)
assert set(ids) >= {"A2", "A4", "A5"}, f"ring not seeded: {ids} — re-run infra/shared/deploy.sh to rebuild the MCP image"
paths = call_mcp_tool("find_paths", {"from_id": "A2", "to_id": "A4", "max_hops": 2})
assert paths, "find_paths returned nothing"
print(f"  graph OK: D2 links {ids}; {len(paths)} A2->A4 path(s)")
PY
echo "✅ pattern 10 ready. Try: make run"
