#!/usr/bin/env bash
# Module 0: shared infra. Idempotent — safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(cd ../.. && pwd)"

PREFIX="$(python3 -c "import json;print(json.load(open('main.parameters.json'))['parameters']['resourcePrefix']['value'])")"
LOCATION="$(python3 -c "import json;print(json.load(open('main.parameters.json'))['parameters']['location']['value'])")"
RG="${PREFIX}-rg"

echo "==> Resource group $RG ($LOCATION)"
az group create -n "$RG" -l "$LOCATION" --tags workshop=reasoning-patterns -o none

echo "==> Pass 1: core infra (no MCP app yet — need the registry first)"
az deployment group create -g "$RG" -f main.bicep -p @main.parameters.json -o none

ACR_NAME="$(az deployment group show -g "$RG" -n main --query properties.outputs.ACR_NAME.value -o tsv)"

echo "==> Building MCP server image in ACR (cloud build, no local Docker needed)"
az acr build -r "$ACR_NAME" -t mcp-server:latest "$REPO_ROOT/common/mcp_server" -o none

echo "==> Pass 2: deploy MCP Container App with the built image"
az deployment group create -g "$RG" -f main.bicep -p @main.parameters.json \
  -p mcpImage="${ACR_NAME}.azurecr.io/mcp-server:latest" -o none

echo "==> Writing shared outputs to $REPO_ROOT/.shared-env"
az deployment group show -g "$RG" -n main --query properties.outputs -o json \
  | python3 -c "
import json,sys
outs=json.load(sys.stdin)
lines=[f'{k}={v[\"value\"]}' for k,v in outs.items()]
lines.append('RESOURCE_GROUP=${RG}')
lines.append('LOCATION=${LOCATION}')
open('${REPO_ROOT}/.shared-env','w').write('\n'.join(lines)+'\n')
print('\n'.join(lines))
"
echo "==> Granting YOU the Search roles the index build runs under"
ME="$(az ad signed-in-user show --query id -o tsv)"
SEARCH_ID="$(az search service show -n "${PREFIX}-search" -g "$RG" --query id -o tsv)"
az role assignment create --assignee "$ME" --role "Search Service Contributor" --scope "$SEARCH_ID" -o none || true
az role assignment create --assignee "$ME" --role "Search Index Data Contributor" --scope "$SEARCH_ID" -o none || true

echo "==> Uploading knowledge corpus + building search index (retries cover RBAC propagation)"
python3 -m pip install -q -r "$REPO_ROOT/common/reasoning_common/requirements.txt" \
  || python3 -m pip install -q --user -r "$REPO_ROOT/common/reasoning_common/requirements.txt"
for attempt in 1 2 3; do
  python3 "$REPO_ROOT/scripts/build_knowledge_index.py" && break
  echo "  attempt $attempt failed (likely role propagation, up to ~10 min) — waiting 90s"
  sleep 90
done

echo "==> Granting YOU Azure AI User on the project (needed for portal + SDK)"
ME="$(az ad signed-in-user show --query id -o tsv)"
PROJ_ID="$(az resource show -g "$RG" -n "${PREFIX}-foundry" --resource-type Microsoft.CognitiveServices/accounts --query id -o tsv)"
az role assignment create --assignee "$ME" --role "Azure AI User" --scope "$PROJ_ID" -o none || true

echo "✅ Shared infra ready. Next: cd patterns/<any> && make deploy"
