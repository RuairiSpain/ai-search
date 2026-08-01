#!/usr/bin/env bash
# Durable mode: provision the Function App and publish the orchestrator.
source "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/lib.sh"
init_pattern_infra "$0"
PREFIX="$(python3 -c "import json;print(json.load(open('$REPO_ROOT/infra/shared/main.parameters.json'))['parameters']['resourcePrefix']['value'])")"

echo "==> Provisioning Function App"
az deployment group create -g "$RESOURCE_GROUP" -f infra/main.bicep \
  -p resourcePrefix="$PREFIX" appInsightsConnectionString="$APPINSIGHTS_CONNECTION_STRING" -o none
FN="$(az deployment group show -g "$RESOURCE_GROUP" -n main --query properties.outputs.functionAppName.value -o tsv 2>/dev/null || echo "${PREFIX}-p08-fn")"

echo "==> Packaging (shared package + pattern src travel with the app)"
rm -rf .funcbuild && mkdir -p .funcbuild
cp -r functions_app/* .funcbuild/
cp -r src .funcbuild/src
cp -r agents skills budgets.yaml variants .funcbuild/
mkdir -p .funcbuild/common && cp -r "$REPO_ROOT/common/reasoning_common" .funcbuild/common/
cat "$REPO_ROOT/common/reasoning_common/requirements.txt" >> .funcbuild/requirements.txt

echo "==> Publishing to $FN (requires Azure Functions Core Tools)"
(cd .funcbuild && func azure functionapp publish "$FN" --python) || {
  echo "func publish failed — install Core Tools (npm i -g azure-functions-core-tools@4) or use local mode."; exit 1; }

echo "==> Granting the Function App identity access to Foundry"
PRINCIPAL="$(az functionapp identity show -g "$RESOURCE_GROUP" -n "$FN" --query principalId -o tsv)"
ACCOUNT_ID="$(az resource show -g "$RESOURCE_GROUP" -n "${PREFIX}-foundry" --resource-type Microsoft.CognitiveServices/accounts --query id -o tsv)"
az role assignment create --assignee "$PRINCIPAL" --role "Azure AI User" --scope "$ACCOUNT_ID" -o none || true

echo "✅ durable mode deployed. Start a claim:"
echo "   curl -X POST https://\$(az functionapp show -g $RESOURCE_GROUP -n $FN --query defaultHostName -o tsv)/api/claims -d @data/sample_input.json"
