using '../main.bicep'

param location = 'westeurope'
param workloadName = 'contoso-rag'
param environmentName = 'prod'
param tags = {
  workload: 'contoso-rag'
  environment: 'prod'
  owner: 'platform-ai'
  costCentre: 'replace-me'
  dataClassification: 'confidential'
  deployedBy: 'bicep'
}

param searchServiceName = 'replace-with-globally-unique-search-name'
param searchSkuName = 'standard'
param replicaCount = 2
param partitionCount = 1
param semanticSearch = 'standard'
param publicNetworkAccess = 'Disabled'
param disableLocalAuth = true
param networkRuleBypass = 'None'
param allowedPublicIpRules = []
param cmkEnforcement = 'Disabled'
param searchIdentityType = 'SystemAssigned'

param searchVnetName = 'vnet-contoso-rag-prod'
param searchVnetAddressPrefixes = [ '10.80.0.0/24' ]
param privateEndpointSubnetName = 'snet-private-endpoints'
param privateEndpointSubnetPrefix = '10.80.0.0/27'
param searchPrivateEndpointName = 'pe-contoso-rag-search-prod'

// Set these when you know your Foundry BYO VNet details.
param foundryVnetName = 'replace-with-foundry-vnet-name'
param foundryVnetResourceGroupName = 'replace-with-foundry-vnet-rg'
param foundryVnetSubscriptionId = 'replace-with-foundry-vnet-subscription-id'
param linkFoundryVnetToPrivateDns = true
param createFoundryVnetPeering = true

param createSampleIndex = false
