// Azure Database for PostgreSQL Flexible Server, Entra-only auth. The
// gateway never holds a Postgres password in Azure — it acquires a token
// for the ossrdbms-aad scope at connect time (src/gateway/store/db.py,
// docs/03-postgres-schema.md "Azure Postgres with Entra auth": tokens
// expire hourly, the app already handles this).
param location string
param tags object
param serverName string
param databaseName string = 'gateway'
param skuName string = 'Standard_B1ms'
param skuTier string = 'Burstable'
param storageSizeGb int = 32
param postgresVersion string = '16'
param gatewayPrincipalId string
param gatewayPrincipalName string
param tenantId string
@description('Extra Entra principal (e.g. your own user objectId) to register as a Postgres AD admin so you can run migrations. Leave empty to skip.')
param extraAdAdminObjectId string = ''
@description('Display name for extraAdAdminObjectId, required if that param is set.')
param extraAdAdminName string = ''
@description('If true, allow inbound connections from any Azure service (novice-friendly default). Turn off and add a VNet/private endpoint before handling real user data — mirror this repo\'s ai-search-private-vnet-bicep modules/private-endpoint.bicep pattern.')
param allowAzureServices bool = true

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuTier
  }
  properties: {
    version: postgresVersion
    storage: { storageSizeGB: storageSizeGb }
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
    }
    highAvailability: { mode: 'Disabled' }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: server
  name: databaseName
}

resource allowAzureServicesRule 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (allowAzureServices) {
  parent: server
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource gatewayAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2024-08-01' = {
  parent: server
  name: gatewayPrincipalId
  properties: {
    principalName: gatewayPrincipalName
    principalType: 'ServicePrincipal'
    tenantId: tenantId
  }
}

resource extraAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2024-08-01' = if (!empty(extraAdAdminObjectId)) {
  parent: server
  name: extraAdAdminObjectId
  properties: {
    principalName: extraAdAdminName
    principalType: 'User'
    tenantId: tenantId
  }
}

output serverFqdn string = server.properties.fullyQualifiedDomainName
output serverName string = server.name
output databaseName string = database.name
