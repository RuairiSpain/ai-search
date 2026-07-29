// Public-network, SAS-gated storage account for the long-duration translation agent.
//
// - Public network access enabled - reachable directly from a caller's browser. There is no
//   broker/proxy in front of it: the hosted agent hands out a fresh, short-lived Blob SAS URL
//   (storage/blob_store.py's generate_download_url) and the browser fetches the blob directly.
// - Security comes entirely from the SAS's signature and expiry, NOT network isolation or
//   anonymous access: allowBlobPublicAccess stays false and the container's publicAccess stays
//   'None'. Anyone holding a valid, unexpired SAS URL can read that one blob - nothing more,
//   until it expires (LDA_DOWNLOAD_SAS_TTL_MINUTES, default 15).
// - The hosted agent's own Managed Identity is the only thing with standing access (RBAC, no
//   account keys): Storage Blob Data Contributor to upload/delete, and Storage Blob Delegator
//   to request the User Delegation Key that signs each SAS (see AzureBlobStore in
//   storage/blob_store.py) - the SAS itself never embeds or requires a long-lived account key.
// - Every blob read/write/delete is logged via a diagnostic setting on the blob service, sent
//   to a Log Analytics workspace - this is how downloads get audited, since the app itself
//   never sees the actual SAS-authenticated read (the browser talks to Storage directly).
// - A lifecycle management policy deletes blobs under artifactTtlDays automatically,
//   independent of the application-level sweeper in cleanup.py.

param location string
param tags object = {}
param storageAccountName string
param containerName string = 'artifacts'
param artifactTtlDays int = 1

param hostedAgentManagedIdentityPrincipalId string = ''

// Read/write/delete access logging (see "Every blob read..." above). Leave
// logAnalyticsWorkspaceId empty to have this module create a dedicated workspace; pass an
// existing workspace's resource id to reuse one instead.
param enableAccessLogging bool = true
param logAnalyticsWorkspaceId string = ''
param logRetentionDays int = 30

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    publicNetworkAccess: 'Enabled'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

resource lifecyclePolicy 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          enabled: true
          name: 'delete-expired-artifacts'
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [
                '${containerName}/users/'
              ]
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: artifactTtlDays
                }
              }
            }
          }
        }
      ]
    }
  }
}

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (enableAccessLogging && empty(logAnalyticsWorkspaceId)) {
  name: '${storageAccountName}-logs'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionDays
  }
}

// Every blob read (SAS-authenticated download), write (upload), and delete is logged here -
// this is the audit trail for "who downloaded what", since the app never sees the actual
// SAS-authenticated GET (the browser talks to Storage directly, not through this app).
resource blobAccessLogging 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (enableAccessLogging) {
  name: 'blob-access-logs'
  scope: blobService
  properties: {
    workspaceId: !empty(logAnalyticsWorkspaceId) ? logAnalyticsWorkspaceId : logAnalyticsWorkspace.id
    logs: [
      {
        category: 'StorageRead'
        enabled: true
      }
      {
        category: 'StorageWrite'
        enabled: true
      }
      {
        category: 'StorageDelete'
        enabled: true
      }
    ]
  }
}

// Grants the hosted agent's managed identity read/write/delete access via RBAC (no account
// keys). Role: Storage Blob Data Contributor.
resource hostedAgentDataRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(hostedAgentManagedIdentityPrincipalId)) {
  name: guid(storageAccount.id, hostedAgentManagedIdentityPrincipalId, 'StorageBlobDataContributor')
  scope: storageAccount
  properties: {
    principalId: hostedAgentManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
    )
  }
}

// Grants permission to request a User Delegation Key - what actually signs the SAS URLs
// generate_download_url hands out. Without this role, get_user_delegation_key() is denied and
// the hosted agent can upload/delete but can never mint a download link. Role: Storage Blob
// Delegator.
resource hostedAgentDelegatorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(hostedAgentManagedIdentityPrincipalId)) {
  name: guid(storageAccount.id, hostedAgentManagedIdentityPrincipalId, 'StorageBlobDelegator')
  scope: storageAccount
  properties: {
    principalId: hostedAgentManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'db58b8e5-c6ad-4a2a-8342-4190687cbf4a'
    )
  }
}

output storageAccountId string = storageAccount.id
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
output logAnalyticsWorkspaceId string = enableAccessLogging && empty(logAnalyticsWorkspaceId) ? logAnalyticsWorkspace.id : logAnalyticsWorkspaceId
