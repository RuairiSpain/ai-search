#!/usr/bin/env bash
set -euo pipefail

# Applies migrations/0001_init.sql (and anything else in migrations/) to the
# gateway's Postgres Flexible Server using YOUR signed-in Azure CLI identity
# over an Entra access token — no password, ever (docs/03-postgres-schema.md
# "Azure Postgres with Entra auth").
#
# Prerequisite: your own Entra objectId must be registered as a Postgres AD
# admin. That happens automatically if you set extraPostgresAdAdminObjectId /
# extraPostgresAdAdminName in infra/config/variables.bicepparam before
# running deploy.sh. If you skipped that, re-run deploy.sh with those set —
# it's an idempotent Bicep deployment.
#
# Usage:
#   ./apply-db-migrations.sh <resource-group> [deployment-name]

RESOURCE_GROUP="${1:?usage: apply-db-migrations.sh <resource-group> [deployment-name]}"
DEPLOYMENT_NAME="${2:-a2a-gateway}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATIONS_DIR="$SCRIPT_DIR/../../migrations"

command -v az >/dev/null 2>&1 || { echo "Azure CLI is required." >&2; exit 1; }
command -v psql >/dev/null 2>&1 || { echo "psql is required (postgresql-client package)." >&2; exit 1; }

echo "Reading Postgres server details from deployment outputs..."
PG_HOST=$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DEPLOYMENT_NAME" \
  --query "properties.outputs.postgresServerFqdn.value" -o tsv)
PG_DB=$(az deployment group show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DEPLOYMENT_NAME" \
  --query "properties.outputs.postgresDatabaseName.value" -o tsv)

SIGNED_IN_USER=$(az account show --query user.name -o tsv)
echo "Connecting to $PG_HOST/$PG_DB as $SIGNED_IN_USER (must be a registered Postgres AD admin)..."

export PGPASSWORD
PGPASSWORD=$(az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv)

for f in "$MIGRATIONS_DIR"/*.sql; do
  echo "Applying $(basename "$f")..."
  psql "host=$PG_HOST port=5432 dbname=$PG_DB user=$SIGNED_IN_USER sslmode=require" -v ON_ERROR_STOP=1 -f "$f"
done

echo "Done."
