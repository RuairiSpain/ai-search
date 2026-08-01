#!/usr/bin/env bash
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
PREFIX="$(python3 -c "import json;print(json.load(open('$REPO_ROOT/infra/shared/main.parameters.json'))['parameters']['resourcePrefix']['value'])")"
az functionapp delete -g "$RESOURCE_GROUP" -n "${PREFIX}-p08-fn" -o none 2>/dev/null || true
az storage account delete -g "$RESOURCE_GROUP" -n "$(echo ${PREFIX}p08fn | tr -d '-')" --yes -o none 2>/dev/null || true
az appservice plan delete -g "$RESOURCE_GROUP" -n "${PREFIX}-p08-plan" --yes -o none 2>/dev/null || true
rm -rf .funcbuild
echo "pattern 08 durable resources removed (local mode needs no teardown)."
