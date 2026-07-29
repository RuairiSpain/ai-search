// Private, artifact-only storage account for the long-duration translation agent.
//
// - Public network access disabled; only reachable via the private endpoint below.
// - The Artifact Broker API (broker/api.py) is the only thing that talks to it,
//   using its Managed Identity (Storage Blob Data Contributor on this account) -
//   never an account key, never a SAS handed to a browser.
// - A lifecycle management policy deletes blobs under artifactTtlDays automatically,
//   independent of the application-level sweeper in cleanup.py.
//
// Mirrors the conventions of ../../modules/private-endpoint.bicep in this repo.

param location string
param tags object = {}
param storageAccountName string
param containerName string = 'artifacts'
param artifactTtlDays int = 1

param subnetId string
param vnetId string
param createPrivateDnsZone bool = true
param privateDnsZoneName string = 'privatelink.blob.${environment().suffixes.storage}'

param brokerManagedIdentityPrincipalId string = ''

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
    }
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

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${storageAccountName}-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${storageAccountName}-blob'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: [
            'blob'
          ]
        }
      }
    ]
  }
}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (createPrivateDnsZone) {
  name: privateDnsZoneName
  location: 'global'
  tags: tags
}

resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (createPrivateDnsZone) {
  name: 'default'
  parent: privateEndpoint
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob-zone'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

resource vnetDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (createPrivateDnsZone) {
  name: 'link-vnet'
  parent: privateDnsZone
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

// Grants the broker's managed identity read/write/delete access via RBAC (no account keys).
// Role: Storage Blob Data Contributor.
resource brokerRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(brokerManagedIdentityPrincipalId)) {
  name: guid(storageAccount.id, brokerManagedIdentityPrincipalId, 'StorageBlobDataContributor')
  scope: storageAccount
  properties: {
    principalId: brokerManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
    )
  }
}

output storageAccountId string = storageAccount.id
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
output privateEndpointId string = privateEndpoint.id
