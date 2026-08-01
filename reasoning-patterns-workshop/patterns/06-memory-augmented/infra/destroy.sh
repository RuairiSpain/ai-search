#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
delete_pattern_agents "p06-" "p06-semantic-"
az storage table delete --name p06Episodic --account-name "$STORAGE_ACCOUNT" --auth-mode login -o none || true
echo "pattern 06 memory cleaned."
