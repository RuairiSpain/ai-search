param location string
param tags object
param privateEndpointName string
param subnetId string
param targetResourceId string
param privateDnsZoneName string
param createPrivateDnsZone bool
param linkSearchVnetToPrivateDns bool
param searchVnetId string
param linkFoundryVnetToPrivateDns bool
param foundryVnetName string
param foundryVnetResourceGroupName string
param foundryVnetSubscriptionId string

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: privateEndpointName
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${privateEndpointName}-search'
        properties: {
          privateLinkServiceId: targetResourceId
          groupIds: [
            'searchService'
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
        name: 'search-zone'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

resource searchVnetDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (createPrivateDnsZone && linkSearchVnetToPrivateDns) {
  name: 'link-search-vnet'
  parent: privateDnsZone
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: searchVnetId
    }
  }
}

resource foundryVnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = if (createPrivateDnsZone && linkFoundryVnetToPrivateDns) {
  name: foundryVnetName
  scope: resourceGroup(foundryVnetSubscriptionId, foundryVnetResourceGroupName)
}

resource foundryVnetDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (createPrivateDnsZone && linkFoundryVnetToPrivateDns) {
  name: 'link-foundry-vnet'
  parent: privateDnsZone
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: foundryVnet.id
    }
  }
}

output privateEndpointId string = privateEndpoint.id
output privateDnsZoneId string = createPrivateDnsZone ? privateDnsZone.id : ''
