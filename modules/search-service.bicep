param location string
param tags object
param name string
param skuName string
param replicaCount int
param partitionCount int
param hostingMode string
param semanticSearch string
param publicNetworkAccess string
param disableLocalAuth bool
param aadAuthFailureMode string
param networkRuleBypass string
param allowedPublicIpRules array
param cmkEnforcement string
param dataExfiltrationProtections array
param identityType string

resource searchService 'Microsoft.Search/searchServices@2025-05-01' = {
  name: name
  location: location
  tags: tags
  identity: identityType == 'None' ? null : {
    type: identityType
  }
  sku: {
    name: skuName
  }
  properties: {
    replicaCount: replicaCount
    partitionCount: partitionCount
    hostingMode: hostingMode
    semanticSearch: semanticSearch
    publicNetworkAccess: publicNetworkAccess
    disableLocalAuth: disableLocalAuth
    authOptions: disableLocalAuth ? null : {
      aadOrApiKey: {
        aadAuthFailureMode: aadAuthFailureMode
      }
    }
    networkRuleSet: {
      bypass: networkRuleBypass
      ipRules: [for ip in allowedPublicIpRules: {
        value: ip
      }]
    }
    encryptionWithCmk: {
      enforcement: cmkEnforcement
    }
    dataExfiltrationProtections: dataExfiltrationProtections
  }
}

output searchServiceId string = searchService.id
output searchEndpoint string = 'https://${name}.search.windows.net'
