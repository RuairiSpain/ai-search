#!/usr/bin/env bash
set -euo pipefail

json_file="/tmp/${INDEX_NAME}.json"
printf '%s' "$INDEX_JSON" > "$json_file"

endpoint="https://${SEARCH_SERVICE_NAME}.search.windows.net"
url="${endpoint}/indexes/${INDEX_NAME}?api-version=${SEARCH_API_VERSION}"

if [[ "${DISABLE_LOCAL_AUTH,,}" == "true" ]]; then
  token="$(az account get-access-token --resource https://search.azure.com --query accessToken -o tsv)"
  curl -sS -X PUT "$url" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${token}" \
    --data-binary "@${json_file}"
else
  admin_key="$(az search admin-key show --service-name "$SEARCH_SERVICE_NAME" --resource-group "$RESOURCE_GROUP_NAME" --query primaryKey -o tsv)"
  curl -sS -X PUT "$url" \
    -H "Content-Type: application/json" \
    -H "api-key: ${admin_key}" \
    --data-binary "@${json_file}"
fi

echo "Created or updated index '${INDEX_NAME}' on '${SEARCH_SERVICE_NAME}'."
