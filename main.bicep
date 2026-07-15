targetScope = 'resourceGroup'

@description('Azure region for all resources that are created by this deployment.')
param location string = resourceGroup().location

@description('Short workload name used for default resource names. Lowercase letters, digits and hyphens are safest.')
param workloadName string = 'aisearch-rag'

@description('Environment name used in tags and default names.')
param environmentName string = 'dev'

@description('Optional standard tags to apply to resources.')
param tags object = {
  workload: workloadName
  environment: environmentName
  deployedBy: 'bicep'
}

// -----------------------------------------------------------------------------
// Azure AI Search service settings
// -----------------------------------------------------------------------------
@description('Globally unique Azure AI Search service name. Leave empty to generate a deterministic name from the resource group.')
param searchServiceName string = ''

@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
  'storage_optimized_l1'
  'storage_optimized_l2'
])
@description('Azure AI Search pricing tier. Private endpoints require Basic or higher; Free is intentionally excluded.')
param searchSkuName string = 'standard'

@description('Replica count for query availability and throughput. Use 2+ for production availability.')
@minValue(1)
param replicaCount int = 1

@description('Partition count for storage and indexing capacity. Use more partitions for larger indexes or heavy indexing.')
@minValue(1)
param partitionCount int = 1

@allowed([
  'default'
  'highDensity'
])
@description('Hosting mode. Use default for most workloads. highDensity is only for supported S3 scenarios with many small indexes.')
param hostingMode string = 'default'

@allowed([
  'free'
  'standard'
  'disabled'
])
@description('Semantic ranker setting. standard enables semantic ranking on supported SKUs/regions. disabled turns it off.')
param semanticSearch string = 'standard'

@allowed([
  'Enabled'
  'Disabled'
])
@description('Public network access for the search data plane. Disabled forces private endpoint access only.')
param publicNetworkAccess string = 'Disabled'

@description('Disable API-key authentication. Default false keeps one-command index setup simple. Set true for keyless-only production patterns after validating RBAC.')
param disableLocalAuth bool = false

@allowed([
  'http401WithBearerChallenge'
  'http403'
])
@description('When Microsoft Entra authentication fails, return either a bearer challenge or generic 403. Only applies when authOptions uses aadOrApiKey.')
param aadAuthFailureMode string = 'http401WithBearerChallenge'

@allowed([
  'None'
  'AzureServices'
])
@description('Network rule bypass for trusted Azure services. Keep None for strict isolation unless a specific service dependency requires bypass.')
param networkRuleBypass string = 'None'

@description('Optional public IP allow list in CIDR form. Usually empty when publicNetworkAccess is Disabled.')
param allowedPublicIpRules array = []

@allowed([
  'Disabled'
  'Enabled'
  'Unspecified'
])
@description('Customer-managed key enforcement setting for Azure AI Search. Disabled uses Microsoft-managed keys.')
param cmkEnforcement string = 'Disabled'

@description('Optional data exfiltration protections. Leave empty unless you know your workload requires a specific protection mode supported by your API version.')
param dataExfiltrationProtections array = []

@allowed([
  'None'
  'SystemAssigned'
])
@description('Managed identity type for the Search service. Use SystemAssigned when you plan to use CMK or keyless service-to-service access.')
param searchIdentityType string = 'SystemAssigned'

// -----------------------------------------------------------------------------
// Network settings
// -----------------------------------------------------------------------------
@description('Name of the VNet that will host the Azure AI Search private endpoint.')
param searchVnetName string = '${workloadName}-${environmentName}-vnet'

@description('Address space for the Search private endpoint VNet.')
param searchVnetAddressPrefixes array = [
  '10.70.0.0/24'
]

@description('Subnet name for Azure AI Search private endpoint.')
param privateEndpointSubnetName string = 'snet-private-endpoints'

@description('Subnet prefix for the private endpoint subnet.')
param privateEndpointSubnetPrefix string = '10.70.0.0/27'

@description('Subnet name delegated to Azure Container Instances so deploymentScripts can run inside the VNet for private data-plane setup.')
param deploymentScriptSubnetName string = 'snet-deployment-scripts'

@description('Subnet prefix for deployment script containers. Must not overlap with other subnets.')
param deploymentScriptSubnetPrefix string = '10.70.0.32/27'

@description('Name of the Azure AI Search private endpoint.')
param searchPrivateEndpointName string = '${workloadName}-${environmentName}-search-pe'

@description('Name of the private DNS zone for Azure AI Search.')
param privateDnsZoneName string = 'privatelink.search.windows.net'

@description('If true, create and link a private DNS zone for the Search private endpoint.')
param createPrivateDnsZone bool = true

@description('If true, link the Search VNet to the private DNS zone.')
param linkSearchVnetToPrivateDns bool = true

// -----------------------------------------------------------------------------
// Placeholder / later binding for Foundry VNet connectivity
// -----------------------------------------------------------------------------
@description('Existing Foundry/BYO VNet name to connect later. Leave empty now; set when you know the Foundry VNet name.')
param foundryVnetName string = ''

@description('Resource group containing the existing Foundry/BYO VNet. Defaults to this resource group.')
param foundryVnetResourceGroupName string = resourceGroup().name

@description('Subscription containing the existing Foundry/BYO VNet. Defaults to the current subscription.')
param foundryVnetSubscriptionId string = subscription().subscriptionId

@description('If true and foundryVnetName is set, link the Foundry VNet to the Azure AI Search private DNS zone.')
param linkFoundryVnetToPrivateDns bool = true

@description('If true and foundryVnetName is set, create VNet peering between the Search VNet and Foundry VNet. Requires permission on both VNets.')
param createFoundryVnetPeering bool = true

@description('Enable forwarded traffic on VNet peerings. Often needed in hub-spoke designs.')
param allowForwardedTraffic bool = true

@description('Allow gateway transit from Search VNet to Foundry VNet. Keep false unless the Search VNet owns a gateway you want to share.')
param allowGatewayTransitFromSearchToFoundry bool = false

@description('Use remote gateways on Search VNet peering to Foundry. Keep false unless Foundry VNet has a gateway and your topology requires it.')
param useRemoteGatewaysOnSearchToFoundry bool = false

// -----------------------------------------------------------------------------
// Optional sample index setup
// -----------------------------------------------------------------------------
@description('Create or update a sample Azure AI Search index after the service is deployed.')
param createSampleIndex bool = true

@description('Sample index name to create. Change or disable createSampleIndex for production indexes.')
param sampleIndexName string = 'documents-index'

@description('API version used for Azure AI Search data-plane index creation.')
param searchDataPlaneApiVersion string = '2024-07-01'

@description('JSON definition for the optional sample index. This is intentionally simple: id, title, content, source, group_ids and a vector field.')
param sampleIndexDefinition object = {
  name: sampleIndexName
  fields: [
    {
      name: 'id'
      type: 'Edm.String'
      key: true
      searchable: false
      filterable: true
      sortable: true
      facetable: false
    }
    {
      name: 'title'
      type: 'Edm.String'
      searchable: true
      filterable: false
      sortable: false
      facetable: false
    }
    {
      name: 'content'
      type: 'Edm.String'
      searchable: true
      filterable: false
      sortable: false
      facetable: false
    }
    {
      name: 'source'
      type: 'Edm.String'
      searchable: true
      filterable: true
      sortable: true
      facetable: true
    }
    {
      name: 'group_ids'
      type: 'Collection(Edm.String)'
      searchable: false
      filterable: true
      facetable: true
    }
    {
      name: 'contentVector'
      type: 'Collection(Edm.Single)'
      searchable: true
      filterable: false
      sortable: false
      facetable: false
      dimensions: 1536
      vectorSearchProfile: 'default-vector-profile'
    }
  ]
  vectorSearch: {
    algorithms: [
      {
        name: 'default-hnsw'
        kind: 'hnsw'
        hnswParameters: {
          metric: 'cosine'
          m: 4
          efConstruction: 400
          efSearch: 500
        }
      }
    ]
    profiles: [
      {
        name: 'default-vector-profile'
        algorithm: 'default-hnsw'
      }
    ]
  }
  semantic: {
    configurations: [
      {
        name: 'default-semantic-config'
        prioritizedFields: {
          titleField: {
            fieldName: 'title'
          }
          prioritizedContentFields: [
            {
              fieldName: 'content'
            }
          ]
          prioritizedKeywordsFields: [
            {
              fieldName: 'source'
            }
          ]
        }
      }
    ]
  }
}

var effectiveSearchServiceName = empty(searchServiceName) ? toLower(take('${replace(workloadName, '_', '-')}-${environmentName}-${uniqueString(resourceGroup().id)}', 60)) : searchServiceName
var foundryVnetConfigured = !empty(foundryVnetName)

module network './modules/network.bicep' = {
  name: 'network'
  params: {
    location: location
    tags: tags
    searchVnetName: searchVnetName
    searchVnetAddressPrefixes: searchVnetAddressPrefixes
    privateEndpointSubnetName: privateEndpointSubnetName
    privateEndpointSubnetPrefix: privateEndpointSubnetPrefix
    deploymentScriptSubnetName: deploymentScriptSubnetName
    deploymentScriptSubnetPrefix: deploymentScriptSubnetPrefix
  }
}

module search './modules/search-service.bicep' = {
  name: 'search-service'
  params: {
    location: location
    tags: tags
    name: effectiveSearchServiceName
    skuName: searchSkuName
    replicaCount: replicaCount
    partitionCount: partitionCount
    hostingMode: hostingMode
    semanticSearch: semanticSearch
    publicNetworkAccess: publicNetworkAccess
    disableLocalAuth: disableLocalAuth
    aadAuthFailureMode: aadAuthFailureMode
    networkRuleBypass: networkRuleBypass
    allowedPublicIpRules: allowedPublicIpRules
    cmkEnforcement: cmkEnforcement
    dataExfiltrationProtections: dataExfiltrationProtections
    identityType: searchIdentityType
  }
}

module privateEndpoint './modules/private-endpoint.bicep' = {
  name: 'search-private-endpoint'
  params: {
    location: location
    tags: tags
    privateEndpointName: searchPrivateEndpointName
    subnetId: network.outputs.privateEndpointSubnetId
    targetResourceId: search.outputs.searchServiceId
    privateDnsZoneName: privateDnsZoneName
    createPrivateDnsZone: createPrivateDnsZone
    linkSearchVnetToPrivateDns: linkSearchVnetToPrivateDns
    searchVnetId: network.outputs.searchVnetId
    linkFoundryVnetToPrivateDns: foundryVnetConfigured && linkFoundryVnetToPrivateDns
    foundryVnetName: foundryVnetName
    foundryVnetResourceGroupName: foundryVnetResourceGroupName
    foundryVnetSubscriptionId: foundryVnetSubscriptionId
  }
}

module peering './modules/foundry-vnet-peering.bicep' = if (foundryVnetConfigured && createFoundryVnetPeering) {
  name: 'foundry-vnet-peering'
  params: {
    searchVnetName: searchVnetName
    searchVnetId: network.outputs.searchVnetId
    foundryVnetName: foundryVnetName
    foundryVnetResourceGroupName: foundryVnetResourceGroupName
    foundryVnetSubscriptionId: foundryVnetSubscriptionId
    allowForwardedTraffic: allowForwardedTraffic
    allowGatewayTransitFromSearchToFoundry: allowGatewayTransitFromSearchToFoundry
    useRemoteGatewaysOnSearchToFoundry: useRemoteGatewaysOnSearchToFoundry
  }
}


resource deployedSearchService 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: effectiveSearchServiceName
}

resource indexSetupIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (createSampleIndex) {
  name: '${effectiveSearchServiceName}-index-setup-id'
  location: location
  tags: tags
}


var deploymentScriptStorageAccountName = toLower(take('st${uniqueString(resourceGroup().id, effectiveSearchServiceName)}', 24))

resource deploymentScriptStorage 'Microsoft.Storage/storageAccounts@2024-01-01' = if (createSampleIndex) {
  name: deploymentScriptStorageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Deny'
      virtualNetworkRules: [
        {
          id: network.outputs.deploymentScriptSubnetId
          action: 'Allow'
        }
      ]
    }
  }
}

resource storageFileDataPrivilegedContributorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = if (createSampleIndex) {
  scope: subscription()
  name: '69566ab7-960f-475b-8e7c-b3118f30c6bd'
}

resource deploymentScriptStorageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createSampleIndex) {
  name: guid(deploymentScriptStorage.id, indexSetupIdentity.id, storageFileDataPrivilegedContributorRole.id)
  scope: deploymentScriptStorage
  properties: {
    roleDefinitionId: storageFileDataPrivilegedContributorRole.id
    principalId: indexSetupIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource searchIndexDataContributorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = if (createSampleIndex && disableLocalAuth) {
  scope: subscription()
  name: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
}

resource searchServiceContributorRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = if (createSampleIndex) {
  scope: subscription()
  name: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
}

resource indexDataRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createSampleIndex && disableLocalAuth) {
  name: guid(search.outputs.searchServiceId, indexSetupIdentity.id, searchIndexDataContributorRole.id)
  scope: deployedSearchService
  properties: {
    roleDefinitionId: searchIndexDataContributorRole.id
    principalId: indexSetupIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
  dependsOn: [
    search
  ]
}

resource serviceContributorRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createSampleIndex) {
  name: guid(search.outputs.searchServiceId, indexSetupIdentity.id, searchServiceContributorRole.id)
  scope: deployedSearchService
  properties: {
    roleDefinitionId: searchServiceContributorRole.id
    principalId: indexSetupIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
  dependsOn: [
    search
  ]
}

resource createIndexScript 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (createSampleIndex) {
  name: '${effectiveSearchServiceName}-create-index'
  location: location
  tags: tags
  kind: 'AzureCLI'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${indexSetupIdentity.id}': {}
    }
  }
  properties: {
    azCliVersion: '2.64.0'
    timeout: 'PT30M'
    cleanupPreference: 'OnSuccess'
    retentionInterval: 'P1D'
    containerSettings: {
      subnetIds: [
        {
          id: network.outputs.deploymentScriptSubnetId
        }
      ]
    }
    storageAccountSettings: {
      storageAccountName: deploymentScriptStorage.name
    }
    environmentVariables: [
      {
        name: 'SEARCH_SERVICE_NAME'
        value: effectiveSearchServiceName
      }
      {
        name: 'SEARCH_API_VERSION'
        value: searchDataPlaneApiVersion
      }
      {
        name: 'INDEX_NAME'
        value: sampleIndexName
      }
      {
        name: 'DISABLE_LOCAL_AUTH'
        value: string(disableLocalAuth)
      }
      {
        name: 'RESOURCE_GROUP_NAME'
        value: resourceGroup().name
      }
      {
        name: 'INDEX_JSON'
        secureValue: string(sampleIndexDefinition)
      }
    ]
    scriptContent: loadTextContent('./scripts/create-index.sh')
  }
  dependsOn: [
    privateEndpoint
    indexDataRoleAssignment
    serviceContributorRoleAssignment
    deploymentScriptStorageRoleAssignment
  ]
}

output searchServiceName string = effectiveSearchServiceName
output searchServiceId string = search.outputs.searchServiceId
output searchEndpoint string = search.outputs.searchEndpoint
output searchPrivateEndpointId string = privateEndpoint.outputs.privateEndpointId
output searchVnetId string = network.outputs.searchVnetId
output privateDnsZoneId string = privateEndpoint.outputs.privateDnsZoneId
output sampleIndexName string = createSampleIndex ? sampleIndexName : ''
