// Pattern 08 (durable mode): Function App on consumption plan + its storage.
// Local mode needs none of this — the same state machine runs in-process.
targetScope = 'resourceGroup'

param resourcePrefix string
param location string = resourceGroup().location
param appInsightsConnectionString string

var tags = { workshop: 'reasoning-patterns', pattern: '08-workflow-state-hitl' }

resource fnStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: replace('${resourcePrefix}p08fn', '-', '')
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${resourcePrefix}-p08-plan'
  location: location
  tags: tags
  sku: { name: 'Y1', tier: 'Dynamic' }
  properties: { reserved: true }
}

resource fn 'Microsoft.Web/sites@2023-12-01' = {
  name: '${resourcePrefix}-p08-fn'
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${fnStorage.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${fnStorage.listKeys().keys[0].value}' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
      ]
    }
  }
}

output functionAppName string = fn.name
output functionAppHostname string = fn.properties.defaultHostName
