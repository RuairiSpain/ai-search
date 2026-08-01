#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
install_shared_reqs   # azure-storage-blob is pinned in the shared requirements.txt

echo "==> Blob container for fan-out checkpoints"
az deployment group create -g "$RESOURCE_GROUP" -f infra/main.bicep \
  -p storageAccountName="$STORAGE_ACCOUNT" -o none
echo "==> Granting you Storage Blob Data Contributor (checkpoint writes)"
ME="$(az ad signed-in-user show --query id -o tsv)"
SA_ID="$(az storage account show -n "$STORAGE_ACCOUNT" -g "$RESOURCE_GROUP" --query id -o tsv)"
az role assignment create --assignee "$ME" --role "Storage Blob Data Contributor" --scope "$SA_ID" -o none || true
echo "✅ pattern 03 ready. Try: make run   (agents are code-side; nothing to register)"
