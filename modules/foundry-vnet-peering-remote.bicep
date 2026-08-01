// Deploys the foundry-side half of the vnet peering. Split out from
// foundry-vnet-peering.bicep because this resource's parent (foundryVnet)
// lives in a different subscription/resource group than the caller's
// deployment scope — Bicep requires that as its own scoped module (BCP165),
// it cannot be a plain child resource declared alongside a differently-scoped
// deployment.
param searchVnetName string
param searchVnetId string
param foundryVnetName string
param allowForwardedTraffic bool

resource foundryVnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: foundryVnetName
}

resource foundryToSearch 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  name: 'peer-to-${searchVnetName}'
  parent: foundryVnet
  properties: {
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: allowForwardedTraffic
    allowGatewayTransit: false
    useRemoteGateways: false
    remoteVirtualNetwork: {
      id: searchVnetId
    }
  }
}
