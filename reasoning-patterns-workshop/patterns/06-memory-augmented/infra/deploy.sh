#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
install_shared_reqs   # azure-data-tables is pinned in the shared requirements.txt

echo "==> Granting YOU 'Storage Table Data Contributor' on the shared storage account"
ME="$(az ad signed-in-user show --query id -o tsv)"
SA_ID="$(az storage account show -n "$STORAGE_ACCOUNT" -g "$RESOURCE_GROUP" --query id -o tsv)"
az role assignment create --assignee "$ME" --role "Storage Table Data Contributor" --scope "$SA_ID" -o none || true
echo "==> Registering per-user agents (one per user_id in memory_seed/)"
python3 - << 'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("../..").resolve() / "common"))
sys.path.insert(0, "src")
from reasoning_common.config import load_variant
import workflow
cfg = load_variant(Path("."), "baseline")
for seed in Path("memory_seed").glob("*.md"):
    aid, semantic_attached = workflow._ensure_agent(cfg, seed.stem)
    status = "with semantic memory" if semantic_attached else "NO semantic memory (vector store unavailable)"
    print(f"  {seed.stem} -> {aid} ({status})")
PY
echo "✅ pattern 06 ready. Try: make run"
