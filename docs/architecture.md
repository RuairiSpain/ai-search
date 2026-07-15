# Architecture

## What gets deployed

```text
Client / Foundry VNet
        |
        | DNS: <search>.search.windows.net -> private IP
        v
Private DNS zone: privatelink.search.windows.net
        |
        v
Search private endpoint subnet
        |
        v
Azure AI Search service
(public network access disabled by default)
```

## Security posture

The default deployment favours private access:

- The Search service is created with `publicNetworkAccess = Disabled`.
- A private endpoint is created for the Search service using group ID `searchService`.
- The Search VNet is linked to the private DNS zone.
- A future Foundry VNet can be linked to DNS and peered to the Search VNet.

## Foundry VNet options

### BYO VNet

When you know your Foundry VNet, set:

```bicep
param foundryVnetName = 'vnet-foundry-dev'
param foundryVnetResourceGroupName = 'rg-foundry-network-dev'
param foundryVnetSubscriptionId = '<subscription-id>'
```

The template can then create:

- DNS link from Foundry VNet to `privatelink.search.windows.net`.
- Bidirectional VNet peering.

### Managed VNet

When Foundry uses managed VNet, configure the Foundry managed network private endpoint/outbound rule from Foundry. This project still creates a private Search endpoint and DNS zone, but managed VNet connectivity is controlled by Foundry's managed network features.

## Why DNS matters

Private endpoint networking is not complete without DNS. Applications usually call:

```text
https://<search-name>.search.windows.net
```

Inside the approved network, that name must resolve to the private endpoint IP. If it resolves to the public IP while public network access is disabled, calls fail.
