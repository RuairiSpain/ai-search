param searchVnetName string
param searchVnetId string
param foundryVnetName string
param foundryVnetResourceGroupName string
param foundryVnetSubscriptionId string
param allowForwardedTraffic bool
param allowGatewayTransitFromSearchToFoundry bool
param useRemoteGatewaysOnSearchToFoundry bool

resource searchVnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: searchVnetName
}

resource foundryVnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: foundryVnetName
  scope: resourceGroup(foundryVnetSubscriptionId, foundryVnetResourceGroupName)
}

resource searchToFoundry 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  name: 'peer-to-${foundryVnetName}'
  parent: searchVnet
  properties: {
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: allowForwardedTraffic
    allowGatewayTransit: allowGatewayTransitFromSearchToFoundry
    useRemoteGateways: useRemoteGatewaysOnSearchToFoundry
    remoteVirtualNetwork: {
      id: foundryVnet.id
    }
  }
}

module foundryToSearch './foundry-vnet-peering-remote.bicep' = {
  name: 'foundry-to-search-peering'
  scope: resourceGroup(foundryVnetSubscriptionId, foundryVnetResourceGroupName)
  params: {
    foundryVnetName: foundryVnetName
    searchVnetName: searchVnetName
    searchVnetId: searchVnetId
    allowForwardedTraffic: allowForwardedTraffic
  }
}
