// Gateway-owned blob storage for artifacts, shared across all three tiers
// (docs/07-artifacts-and-code-interpreter.md). The lifecycle policy
// implements D5's default retention (90d, then cool, delete at 365d) on
// the `artifacts/` prefix; per-app overrides are a gwlint/config concern,
// not infra.
param location string
param tags object
param storageAccountName string
param artifactsContainerName string = 'artifacts'
// Principal id granted Storage Blob Data Contributor on this account.
param gatewayPrincipalId string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    // Public network access left enabled for a novice-friendly default,
    // matching this repo's top-level Azure AI Search project. Tighten to
    // a private endpoint before handling real user data — see that
    // project's modules/private-endpoint.bicep for the pattern to copy.
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    // Never hand out raw blob URLs (docs/07 §2) — deleteRetentionPolicy
    // is a safety net for operator error, not the access-control layer.
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource artifactsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: artifactsContainerName
  properties: {
    publicAccess: 'None'
  }
}

// D5: 90d -> cool, delete at 365d, matched on the artifacts/ prefix.
resource lifecyclePolicy 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'artifact-retention'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['${artifactsContainerName}/']
            }
            actions: {
              baseBlob: {
                tierToCool: { daysAfterModificationGreaterThan: 90 }
                delete: { daysAfterModificationGreaterThan: 365 }
              }
            }
          }
        }
      ]
    }
  }
}

// Storage Blob Data Contributor
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, gatewayPrincipalId, storageBlobDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: gatewayPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output storageAccountId string = storage.id
output storageAccountName string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob
