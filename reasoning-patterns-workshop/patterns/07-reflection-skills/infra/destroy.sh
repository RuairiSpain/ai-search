#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
delete_pattern_agents "p07-"
echo "pattern 07 agents removed. Skill library left in place — reset with 'make reset-library'."
