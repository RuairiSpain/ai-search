#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
az storage container delete --name p03-state --account-name "$STORAGE_ACCOUNT" --auth-mode login -o none || true
echo "pattern 03 state container removed."
