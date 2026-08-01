#!/usr/bin/env bash
# Sanity-check the role assignments the workshop depends on.
set -euo pipefail
source "$(dirname "$0")/../.shared-env"
echo "Role assignments on $RESOURCE_GROUP:"
az role assignment list -g "$RESOURCE_GROUP" \
  --query "[].{principal:principalName,role:roleDefinitionName,scope:scope}" -o table
echo
echo "Expect to see: your user with 'Azure AI User'; the project identity with"
echo "'Search Index Data Contributor' and 'Storage Blob Data Contributor'."
