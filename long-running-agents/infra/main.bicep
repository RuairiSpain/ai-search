targetScope = 'resourceGroup'

// =============================================================================
// A2A Gateway infrastructure
// =============================================================================
// Provisions everything the gateway needs EXCEPT the Foundry project/agents
// themselves (those are deployed separately per docs/04-06, via azd/SDK) and
// EXCEPT the per-agent UserIdentityImpersonation grant, which targets a
// resource (the Foundry agent endpoint) that doesn't exist until an agent is
// deployed — see infra/scripts/grant-agent-access.sh and
// docs/05-tier2-hosted-agents.md §6.2 "RBAC provisioning automation".
//
// Resources created here:
//   - a user-assigned managed identity for the gateway
//   - Log Analytics + Application Insights
//   - Azure Database for PostgreSQL Flexible Server (Entra-only auth)
//   - a Storage account with the shared `artifacts` blob container + lifecycle policy
//   - a Key Vault (RBAC-authorized)
//   - a Container Apps environment + the gateway Container App
// =============================================================================

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short workload name used for default resource names.')
param workloadName string = 'a2a-gw'

@description('Environment name used in tags and default names.')
param environmentName string = 'dev'

@description('Tags applied to every resource.')
param tags object = {
  workload: workloadName
  environment: environmentName
  deployedBy: 'bicep'
}

// -----------------------------------------------------------------------------
// Gateway identity / auth
// -----------------------------------------------------------------------------
@description('Tenant ID that issues tokens for the chat clients calling this gateway.')
param gatewayTenantId string

@description('The gateway\'s own app registration audience. Chat clients request a token for this, never for https://ai.azure.com — see docs/00-tier-model-and-concepts.md §5.')
param gatewayAudience string = 'api://a2a-gateway'

@description('Your own Entra objectId, registered as an extra Postgres AD admin so you can run migrations from a workstation. Leave empty to skip.')
param extraPostgresAdAdminObjectId string = ''
param extraPostgresAdAdminName string = ''

// -----------------------------------------------------------------------------
// Foundry
// -----------------------------------------------------------------------------
@description('The Foundry project endpoint the gateway calls. Provision the Foundry project itself separately (docs/04-06); this only wires the endpoint into the gateway container.')
param foundryProjectEndpoint string

// -----------------------------------------------------------------------------
// Postgres
// -----------------------------------------------------------------------------
@description('Postgres Flexible Server SKU name. Burstable B1ms is fine for dev; move to a General Purpose tier before production.')
param postgresSkuName string = 'Standard_B1ms'
param postgresSkuTier string = 'Burstable'
param postgresStorageSizeGb int = 32

// -----------------------------------------------------------------------------
// Container App
// -----------------------------------------------------------------------------
@description('Gateway container image. Defaults to a placeholder; build and push your own with infra/deploy.sh before going further than a smoke test.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param containerAppCpu string = '0.5'
param containerAppMemory string = '1.0Gi'
param containerAppMinReplicas int = 1
param containerAppMaxReplicas int = 3

// -----------------------------------------------------------------------------
// Names (deterministic from workloadName + environmentName + a short hash so
// they stay globally unique for the resources that require it)
// -----------------------------------------------------------------------------
var suffix = uniqueString(resourceGroup().id, workloadName, environmentName)
var namePrefix = '${workloadName}-${environmentName}'
var storageAccountName = take(toLower(replace('${workloadName}${environmentName}${suffix}', '-', '')), 24)
var keyVaultName = take('${namePrefix}-kv-${suffix}', 24)
var postgresServerName = '${namePrefix}-pg-${suffix}'
var identityName = '${namePrefix}-id'
var registryName = toLower(replace('${namePrefix}acr${suffix}', '-', ''))
var logAnalyticsName = '${namePrefix}-log'
var appInsightsName = '${namePrefix}-appi'
var containerEnvName = '${namePrefix}-env'
var containerAppName = '${namePrefix}-app'

// -----------------------------------------------------------------------------
// Modules
// -----------------------------------------------------------------------------
module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    location: location
    tags: tags
    name: identityName
  }
}

module registry 'modules/registry.bicep' = {
  name: 'registry'
  params: {
    location: location
    tags: tags
    registryName: registryName
    gatewayPrincipalId: identity.outputs.principalId
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    tags: tags
    workspaceName: logAnalyticsName
    appInsightsName: appInsightsName
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    tags: tags
    storageAccountName: storageAccountName
    gatewayPrincipalId: identity.outputs.principalId
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    location: location
    tags: tags
    keyVaultName: keyVaultName
    gatewayPrincipalId: identity.outputs.principalId
    tenantId: subscription().tenantId
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    location: location
    tags: tags
    serverName: postgresServerName
    skuName: postgresSkuName
    skuTier: postgresSkuTier
    storageSizeGb: postgresStorageSizeGb
    gatewayPrincipalId: identity.outputs.principalId
    gatewayPrincipalName: identityName
    tenantId: subscription().tenantId
    extraAdAdminObjectId: extraPostgresAdAdminObjectId
    extraAdAdminName: extraPostgresAdAdminName
  }
}

module containerApp 'modules/container-app.bicep' = {
  name: 'container-app'
  params: {
    location: location
    tags: tags
    environmentName: containerEnvName
    containerAppName: containerAppName
    containerImage: containerImage
    logAnalyticsWorkspaceName: logAnalyticsName
    gatewayIdentityId: identity.outputs.identityId
    gatewayIdentityClientId: identity.outputs.clientId
    cpuCores: containerAppCpu
    memory: containerAppMemory
    minReplicas: containerAppMinReplicas
    maxReplicas: containerAppMaxReplicas
    foundryProjectEndpoint: foundryProjectEndpoint
    postgresHost: postgres.outputs.serverFqdn
    postgresDatabase: postgres.outputs.databaseName
    postgresUser: identityName
    artifactsStorageAccountUrl: storage.outputs.blobEndpoint
    artifactsContainerName: 'artifacts'
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    gatewayTenantId: gatewayTenantId
    gatewayAudience: gatewayAudience
    registryServer: registry.outputs.loginServer
  }
  dependsOn: [monitoring, storage, postgres, registry]
}

// -----------------------------------------------------------------------------
// Outputs — feed these into infra/scripts/*.sh
// -----------------------------------------------------------------------------
output gatewayIdentityPrincipalId string = identity.outputs.principalId
output gatewayIdentityClientId string = identity.outputs.clientId
output postgresServerFqdn string = postgres.outputs.serverFqdn
output postgresDatabaseName string = postgres.outputs.databaseName
output storageAccountName string = storage.outputs.storageAccountName
output keyVaultUri string = keyVault.outputs.keyVaultUri
output registryLoginServer string = registry.outputs.loginServer
output containerAppFqdn string = containerApp.outputs.containerAppFqdn
output containerAppName string = containerApp.outputs.containerAppName
