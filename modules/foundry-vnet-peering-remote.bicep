// Deploys the Foundry-side half of the VNet peering. Split out from
// foundry-vnet-peering.bicep because this resource must be deployed into the
// Foundry VNet's own resource group/subscription (Bicep requires a nested
// module - not a plain `parent:`/`scope:` on the resource itself - to deploy
// into a scope different from the calling file's).
param foundryVnetName string
param searchVnetName string
param searchVnetId string
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
