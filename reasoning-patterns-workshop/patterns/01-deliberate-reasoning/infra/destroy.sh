#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
delete_pattern_agents "p01-"
echo "pattern 01 agents removed (shared infra untouched)."
