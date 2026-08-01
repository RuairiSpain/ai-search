#!/usr/bin/env bash
set -euo pipefail

# Grants the gateway's managed identity permission to act as delegated
# end users against ONE Foundry agent — the single manual step that makes
# T2 (and, per D1, T1) per-user isolation actually take effect. Missing
# this is silent: the gateway calls succeed, but every user collapses into
# one shared sandbox (docs/00-tier-model-and-concepts.md §2, docs/05
# §6.2 "RBAC provisioning automation").
#
# This is deliberately NOT in main.bicep: it targets a Foundry AI Services
# account/agent that is provisioned separately (docs/04-06, via
# `azd ai agent init` / `create_version()`), so it can't be a dependency of
# the core infra deployment. Run this once per Foundry account after each
# new account is provisioned, or per agent if your access model needs
# finer scoping than the account.
#
# Usage:
#   ./grant-agent-access.sh <resource-group> <foundry-account-resource-id> [gateway-principal-id]
#
# <foundry-account-resource-id> looks like:
#   /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account-name>
# Get it with:
#   az cognitiveservices account show -g <rg> -n <account-name> --query id -o tsv
#
# The action string below comes straight from
# docs/05-tier2-hosted-agents.md §3 and docs/02-decisions.md D1:
#   Microsoft.CognitiveServices/accounts/AIServices/agents/endpoints/UserIdentityImpersonation/action
#
# ⚠ Whether this is registered as a control-plane Action or a DataAction is
# one of this project's open items (docs/08-open-items-and-experiments.md,
# "RBAC provisioning automation"). This script tries Actions first (the
# `.../action` suffix is that convention) and tells you exactly what to
# flip if `az role definition create` rejects it.

RESOURCE_GROUP="${1:?usage: grant-agent-access.sh <resource-group> <foundry-account-resource-id> [gateway-principal-id]}"
FOUNDRY_ACCOUNT_ID="${2:?usage: grant-agent-access.sh <resource-group> <foundry-account-resource-id> [gateway-principal-id]}"
GATEWAY_PRINCIPAL_ID="${3:-}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-a2a-gateway}"
ROLE_NAME="A2A Gateway - User Identity Impersonation"
ACTION="Microsoft.CognitiveServices/accounts/AIServices/agents/endpoints/UserIdentityImpersonation/action"

command -v az >/dev/null 2>&1 || { echo "Azure CLI is required." >&2; exit 1; }

if [[ -z "$GATEWAY_PRINCIPAL_ID" ]]; then
  echo "No gateway-principal-id given; reading it from deployment outputs..."
  GATEWAY_PRINCIPAL_ID=$(az deployment group show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$DEPLOYMENT_NAME" \
    --query "properties.outputs.gatewayIdentityPrincipalId.value" -o tsv)
fi

SUBSCRIPTION_ID=$(az account show --query id -o tsv)

echo "Ensuring custom role \"$ROLE_NAME\" exists, scoped to $FOUNDRY_ACCOUNT_ID..."
EXISTING_ROLE=$(az role definition list --name "$ROLE_NAME" --scope "$FOUNDRY_ACCOUNT_ID" --query "[0].id" -o tsv || true)

if [[ -z "$EXISTING_ROLE" ]]; then
  ROLE_JSON=$(mktemp)
  ERR_LOG=$(mktemp)
  trap 'rm -f "$ROLE_JSON" "$ERR_LOG"' EXIT
  cat > "$ROLE_JSON" <<EOF
{
  "Name": "$ROLE_NAME",
  "IsCustom": true,
  "Description": "Lets the A2A gateway's managed identity delegate a verified end-user identity into hosted-agent (T2) sessions and, per D1, Responses calls generally. Grants nothing else.",
  "Actions": ["$ACTION"],
  "NotActions": [],
  "DataActions": [],
  "NotDataActions": [],
  "AssignableScopes": ["$FOUNDRY_ACCOUNT_ID"]
}
EOF

  if ! az role definition create --role-definition "$ROLE_JSON" >/dev/null 2>"$ERR_LOG"; then
    if grep -qi "not a valid action" "$ERR_LOG" || grep -qi "DataAction" "$ERR_LOG"; then
      echo "The action was rejected as a control-plane Action. Retrying as a DataAction..." >&2
      python3 - "$ROLE_JSON" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f:
    role = json.load(f)
action = role["Actions"].pop()
role["DataActions"].append(action)
with open(path, "w") as f:
    json.dump(role, f)
PY
      az role definition create --role-definition "$ROLE_JSON" >/dev/null
    else
      cat "$ERR_LOG" >&2
      exit 1
    fi
  fi
  echo "Created role definition."
else
  echo "Role definition already exists ($EXISTING_ROLE)."
fi

echo "Assigning \"$ROLE_NAME\" to principal $GATEWAY_PRINCIPAL_ID on $FOUNDRY_ACCOUNT_ID..."
az role assignment create \
  --assignee-object-id "$GATEWAY_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "$ROLE_NAME" \
  --scope "$FOUNDRY_ACCOUNT_ID" \
  --only-show-errors >/dev/null

echo "Done. Verify with the gateway's own startup probe (docs/05 §3.4 'health()'):"
echo "  it fails readiness if this grant is missing or hasn't propagated yet"
echo "  (RBAC propagation can take a few minutes)."
