# Operations

## Deploy

```bash
./deploy.sh
```

Custom:

```bash
./deploy.sh rg-my-search-dev westeurope config/variables.bicepparam
```

## Validate Search service

```bash
az search service show \
  --resource-group rg-aisearch-private-dev \
  --name <search-name> \
  --query "{name:name, publicNetworkAccess:publicNetworkAccess, sku:sku.name, replicas:replicaCount, partitions:partitionCount}"
```

## Validate private endpoint

```bash
az network private-endpoint list \
  --resource-group rg-aisearch-private-dev \
  -o table
```

## Validate DNS from inside the VNet

```bash
nslookup <search-name>.search.windows.net
```

Expected: a private IP from the private endpoint subnet.

## Add Foundry VNet later

Edit `config/variables.bicepparam`:

```bicep
param foundryVnetName = 'vnet-foundry-dev'
param foundryVnetResourceGroupName = 'rg-foundry-network-dev'
param foundryVnetSubscriptionId = '<subscription-id>'
```

Redeploy:

```bash
./deploy.sh rg-aisearch-private-dev westeurope config/variables.bicepparam
```

## Troubleshooting

### Search calls fail even though private endpoint exists

Most likely DNS is wrong. From the caller network, run:

```bash
nslookup <search-name>.search.windows.net
```

It should resolve through `privatelink.search.windows.net` to a private IP.

### Peering deployment fails

Check:

- Foundry VNet name/resource group/subscription are correct.
- Address spaces do not overlap.
- Your identity can create peering on both VNets.
- Your organisation does not require all peering via a central hub template.

### Sample index creation fails with public access disabled

Deployment scripts run from Azure-managed infrastructure. If the Search public endpoint is disabled, data-plane setup may require either:

- keyless/RBAC plus an execution environment that can reach the private endpoint, or
- running index creation from a VM/build agent inside the VNet.

The infrastructure deployment still succeeds; you can set `createSampleIndex = false` and run index creation from a network-connected agent.
