param location string
param tags object
param searchVnetName string
param searchVnetAddressPrefixes array
param privateEndpointSubnetName string
param privateEndpointSubnetPrefix string
param deploymentScriptSubnetName string
param deploymentScriptSubnetPrefix string

resource searchVnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: searchVnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: searchVnetAddressPrefixes
    }
    subnets: [
      {
        name: privateEndpointSubnetName
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: deploymentScriptSubnetName
        properties: {
          addressPrefix: deploymentScriptSubnetPrefix
          serviceEndpoints: [
            {
              service: 'Microsoft.Storage'
            }
          ]
          delegations: [
            {
              name: 'aci-delegation'
              properties: {
                serviceName: 'Microsoft.ContainerInstance/containerGroups'
              }
            }
          ]
        }
      }
    ]
  }
}

output searchVnetId string = searchVnet.id
output privateEndpointSubnetId string = searchVnet.properties.subnets[0].id
output deploymentScriptSubnetId string = searchVnet.properties.subnets[1].id
