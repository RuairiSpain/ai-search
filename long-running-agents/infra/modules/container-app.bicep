// Container Apps environment + the gateway itself. Chosen over Functions
// or a raw VM because the gateway holds SSE connections per active task
// (docs/02-decisions.md D3) and needs to be always-on with a managed
// identity — Container Apps gives both without managing a VM.
param location string
param tags object
param environmentName string
param containerAppName string
@description('Publicly pullable placeholder until you build and push the real gateway image (see infra/deploy.sh).')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param logAnalyticsWorkspaceName string
param gatewayIdentityId string
param gatewayIdentityClientId string
param cpuCores string = '0.5'
param memory string = '1.0Gi'
param minReplicas int = 1
param maxReplicas int = 3
param foundryProjectEndpoint string
param postgresHost string
param postgresDatabase string
param postgresUser string
param artifactsStorageAccountUrl string
param artifactsContainerName string
param appInsightsConnectionString string
param gatewayTenantId string
param gatewayAudience string = 'api://a2a-gateway'
@description('ACR login server to pull containerImage from. Leave empty when using a public placeholder image.')
param registryServer string = ''

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${gatewayIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto' // allows the SSE follow endpoint to work over HTTP/1.1
      }
      registries: empty(registryServer) ? [] : [
        {
          server: registryServer
          identity: gatewayIdentityId
        }
      ]
    }
    template: {
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
      containers: [
        {
          name: 'gateway'
          image: containerImage
          resources: {
            cpu: json(cpuCores)
            memory: memory
          }
          env: [
            { name: 'AZURE_CLIENT_ID', value: gatewayIdentityClientId } // pins DefaultAzureCredential to this UAMI
            { name: 'GATEWAY_TENANT_ID', value: gatewayTenantId }
            { name: 'GATEWAY_CONFIG_PATH', value: 'config/apps.yaml' }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            { name: 'PGHOST', value: postgresHost }
            { name: 'PGDATABASE', value: postgresDatabase }
            { name: 'PGUSER', value: postgresUser }
            { name: 'PG_USE_ENTRA_AUTH', value: 'true' }
            { name: 'ARTIFACTS_STORAGE_ACCOUNT_URL', value: artifactsStorageAccountUrl }
            { name: 'ARTIFACTS_CONTAINER', value: artifactsContainerName }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'LOG_LEVEL', value: 'info' }
          ]
          probes: [
            {
              type: 'Readiness'
              httpGet: { path: '/healthz', port: 8080 }
              periodSeconds: 15
            }
          ]
        }
      ]
    }
  }
}

output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppName string = containerApp.name
