// Log Analytics + Application Insights. The gateway and (separately) the
// T2/T3 upstream containers should all emit OpenTelemetry into the same
// workspace so trace correlation is even possible — see
// docs/05-tier2-hosted-agents.md §6.3 and docs/06-tier3-durable-agents.md
// §6.3 "the gap to close first". This module only provisions the sink;
// wiring traceparent propagation end to end is application code, not infra.
param location string
param tags object
param workspaceName string
param appInsightsName string

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

output logAnalyticsWorkspaceId string = logAnalytics.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
