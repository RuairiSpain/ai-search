#!/usr/bin/env bash
set -euo pipefail

# One-command deployment wrapper.
# Usage:
#   ./deploy.sh
#   ./deploy.sh my-resource-group westeurope config/variables.bicepparam

RESOURCE_GROUP="${1:-rg-aisearch-private-dev}"
LOCATION="${2:-westeurope}"
PARAM_FILE="${3:-config/variables.bicepparam}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-aisearch-private-vnet}"

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required. Install it from Microsoft Learn, then run az login." >&2
  exit 1
fi

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --only-show-errors >/dev/null

az deployment group create \
  --name "$DEPLOYMENT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --template-file main.bicep \
  --parameters "$PARAM_FILE" \
  --query "properties.outputs" \
  --output json
