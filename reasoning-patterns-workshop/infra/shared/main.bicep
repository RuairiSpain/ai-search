// Module 0 — shared workshop infrastructure.
// Scope: resource group (created by deploy.sh).
// Everything pattern folders need to exist exactly once.

@description('Prefix for all resource names (3-12 lowercase alphanumerics).')
@minLength(3)
@maxLength(12)
param resourcePrefix string

@description('Region. Must have quota for the models below — see WORKSHOP.md.')
param location string = resourceGroup().location

@description('Container image for the MCP server, pushed by deploy.sh. Empty on first pass.')
param mcpImage string = ''

var tags = { workshop: 'reasoning-patterns', pattern: 'shared' }

// ---------- Observability ----------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${resourcePrefix}-logs'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${resourcePrefix}-ai'
  location: location
  tags: tags
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}

// ---------- Storage (eval datasets, knowledge docs, agent state) ----------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: replace('${resourcePrefix}wkshpsa', '-', '')
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false // AAD-only: every caller in this repo uses DefaultAzureCredential
    minimumTlsVersion: 'TLS1_2'
  }
}

// ---------- Azure AI Search (knowledge bases / Foundry IQ backing index) ----------
resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: '${resourcePrefix}-search'
  location: location
  tags: tags
  sku: { name: 'basic' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    authOptions: { aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' } }
  }
}

// ---------- Foundry account + project ----------
// Foundry (new, project-based) = Cognitive Services account kind 'AIServices'
// with allowProjectManagement, plus a child project resource.
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: '${resourcePrefix}-foundry'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: '${resourcePrefix}-foundry'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false // WORKSHOP ONLY — set true (Entra-only) in production, see SECURITY.md
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: '${resourcePrefix}-proj'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'Reasoning Patterns Workshop' }
}

// ---------- Model deployments ----------
// Names are the *deployment* names patterns reference in variants/*.yaml.
// Swap model/version here if your region lacks quota — variants files use
// deployment names, so nothing else changes.
@description('Model deployments. Edit versions to match current catalogue.')
param modelDeployments array = [
  { name: 'frontier',    model: 'gpt-5.1',          format: 'OpenAI',    capacity: 50 }
  { name: 'small',       model: 'gpt-5-mini',       format: 'OpenAI',    capacity: 100 }
  { name: 'nano',        model: 'gpt-5-nano',       format: 'OpenAI',    capacity: 100 }
  { name: 'reviewer',    model: 'claude-haiku-4-5', format: 'Anthropic', capacity: 50 }
  { name: 'router',      model: 'model-router',     format: 'OpenAI',    capacity: 50 }
]

@batchSize(1) // serial: parallel model deployments race on account updates
resource deployments 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [
  for d in modelDeployments: {
    parent: foundry
    name: d.name
    sku: { name: 'GlobalStandard', capacity: d.capacity }
    // No explicit version: resolves to the catalogue default. Pin versions here
    // once quota is confirmed — unpinned models can change under a workshop.
    properties: { model: { name: d.model, format: d.format } }
  }
]

// ---------- Container Apps env + MCP server ----------
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: replace('${resourcePrefix}acr', '-', '')
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true } // WORKSHOP ONLY — production uses managed identity + AcrPull, see SECURITY.md
}

resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${resourcePrefix}-aca-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource mcpApp 'Microsoft.App/containerApps@2024-03-01' = if (!empty(mcpImage)) {
  name: '${resourcePrefix}-mcp'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'http' }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.name
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [ { name: 'acr-password', value: acr.listCredentials().passwords[0].value } ]
    }
    template: {
      containers: [
        {
          name: 'mcp-server'
          image: mcpImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
}

// ---------- Role assignments the whole workshop relies on ----------
var roleSearchDataContrib = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
var roleBlobDataContrib   = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')

resource projSearchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, project.id, roleSearchDataContrib)
  scope: search
  properties: {
    roleDefinitionId: roleSearchDataContrib
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource projBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, project.id, roleBlobDataContrib)
  scope: storage
  properties: {
    roleDefinitionId: roleBlobDataContrib
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------- Outputs → .shared-env ----------
output FOUNDRY_ACCOUNT_NAME string = foundry.name
output FOUNDRY_PROJECT_NAME string = project.name
output FOUNDRY_PROJECT_ENDPOINT string = 'https://${foundry.properties.customSubDomainName}.services.ai.azure.com/api/projects/${project.name}'
output FOUNDRY_OPENAI_ENDPOINT string = foundry.properties.endpoint
output SEARCH_ENDPOINT string = 'https://${search.name}.search.windows.net'
output SEARCH_NAME string = search.name
output STORAGE_ACCOUNT string = storage.name
output APPINSIGHTS_CONNECTION_STRING string = appInsights.properties.ConnectionString
output ACR_LOGIN_SERVER string = acr.properties.loginServer
output ACR_NAME string = acr.name
output ACA_ENV_NAME string = acaEnv.name
output MCP_SERVER_URL string = empty(mcpImage) ? '' : 'https://${mcpApp.properties.configuration.ingress.fqdn}'
