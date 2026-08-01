#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
install_shared_reqs

python3 - << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("../..").resolve() / "common"))
from reasoning_common.mcp_client import call_mcp_tool
g = call_mcp_tool("get_oauth_grants", {"user": "mchen"})
apps = {x["app"] for x in g}
assert "MailSyncPro" in apps, f"identity route missing: {apps} — re-run infra/shared/deploy.sh"
print(f"  identity tools OK ({len(g)} grants for mchen)")
PY
echo "✅ pattern 05 ready. Try: make run"
