#!/usr/bin/env bash
set -euo pipefail

# One-command infra deployment for the A2A gateway.
#
# Usage:
#   ./deploy.sh
#   ./deploy.sh <resource-group> <region> <param-file>
#   ./deploy.sh --build   # also builds+pushes the gateway image to ACR
#                         # and redeploys the Container App to use it
#
# This provisions infra only. Foundry projects/agents are provisioned
# separately (docs/04-06), and per-agent RBAC (UserIdentityImpersonation)
# is a distinct step — see scripts/grant-agent-access.sh.

BUILD_IMAGE=false
if [[ "${1:-}" == "--build" ]]; then
  BUILD_IMAGE=true
  shift
fi

RESOURCE_GROUP="${1:-rg-a2a-gateway-dev}"
LOCATION="${2:-westeurope}"
PARAM_FILE="${3:-config/variables.bicepparam}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-a2a-gateway}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required. Install it from Microsoft Learn, then run az login." >&2
  exit 1
fi

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --only-show-errors >/dev/null

echo "Deploying infra (identity, Postgres, storage, Key Vault, ACR, Container App)..."
DEPLOY_OUTPUT=$(az deployment group create \
  --name "$DEPLOYMENT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --template-file main.bicep \
  --parameters "$PARAM_FILE" \
  --query "properties.outputs" \
  --output json)

echo "$DEPLOY_OUTPUT"

REGISTRY_LOGIN_SERVER=$(echo "$DEPLOY_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['registryLoginServer']['value'])")
CONTAINER_APP_NAME=$(echo "$DEPLOY_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['containerAppName']['value'])")
REGISTRY_NAME="${REGISTRY_LOGIN_SERVER%%.*}"

if [[ "$BUILD_IMAGE" == "true" ]]; then
  echo "Building gateway image in ACR ($REGISTRY_NAME)..."
  IMAGE_TAG="$REGISTRY_LOGIN_SERVER/a2a-gateway:$(git -C .. rev-parse --short HEAD 2>/dev/null || date +%s)"
  az acr build \
    --registry "$REGISTRY_NAME" \
    --image "a2a-gateway:$(git -C .. rev-parse --short HEAD 2>/dev/null || date +%s)" \
    --file ../Dockerfile \
    ..

  echo "Pointing the Container App at $IMAGE_TAG..."
  az containerapp update \
    --name "$CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --image "$IMAGE_TAG" \
    --only-show-errors >/dev/null
  echo "Deployed $IMAGE_TAG"
fi

echo ""
echo "Next steps:"
echo "  1. Apply the Postgres schema:   ./scripts/apply-db-migrations.sh $RESOURCE_GROUP"
echo "  2. Deploy your Foundry agents (docs/04-06), then permission each one:"
echo "     ./scripts/grant-agent-access.sh $RESOURCE_GROUP <foundry-agent-resource-id>"
