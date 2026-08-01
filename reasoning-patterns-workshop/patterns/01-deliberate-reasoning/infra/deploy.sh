#!/usr/bin/env bash
# Pattern 01 deploy: deps, playground agent, hosted-agent container.
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
install_shared_reqs

echo "==> Registering PLAYGROUND agent (declarative prompt agent, §6 runtime row 1)"
python3 - << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("../..").resolve() / "common"))
from reasoning_common import foundry_client as fc
aid = fc.upsert_agent(
    "p01-deliberate-playground",
    deployment="small",
    instructions_path=Path("agents/deliberate-diagnostician/instructions.md"),
    description="Pattern 01 candidate generator — try it solo in the playground, then compare with the full loop.",
)
print(f"playground agent: {aid}")
PY

echo "==> Building HOSTED agent image (custom loop, §6 runtime row 2)"
az acr build -r "$ACR_NAME" -t p01-deliberate:latest -f patterns/01-deliberate-reasoning/Dockerfile.hosted "$REPO_ROOT" -o none || {
  echo "ACR build failed — you can still run the loop locally with 'make run'."; }

# VOLATILE SURFACE: hosted-agent registration CLI/API is evolving. The current
# docs live at https://learn.microsoft.com/azure/foundry/agents — if the command
# below fails, register the container as a hosted agent in the portal
# (Agents -> New -> Hosted) pointing at ${ACR_LOGIN_SERVER}/p01-deliberate:latest.
az foundry agent create \
  --project "$FOUNDRY_PROJECT_NAME" --account "$FOUNDRY_ACCOUNT_NAME" \
  --name p01-deliberate-hosted \
  --image "${ACR_LOGIN_SERVER}/p01-deliberate:latest" 2>/dev/null \
  || echo "(hosted-agent CLI not available in this tenant yet — see note above)"

echo "✅ pattern 01 deployed. Try: make run"
