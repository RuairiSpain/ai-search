# Azure AI Search behind a private VNet for Microsoft Foundry

This project deploys an Azure AI Search service for secure RAG/agent workloads:

- Azure AI Search with configurable SKU, replicas, partitions, auth, semantic ranker, CMK enforcement and network settings.
- Public network access disabled by default.
- Private endpoint for Azure AI Search in a dedicated VNet/subnet.
- Private DNS zone `privatelink.search.windows.net` and VNet links.
- Optional placeholder parameters to connect a Foundry/BYO VNet later.
- Optional sample index creation as part of the same deployment.

The idea is that a novice can edit **one documented parameter file** and deploy with **one command**.

## Quick start

```bash
az login
cd ai-search-private-vnet-bicep
./deploy.sh
```

By default, this creates resource group `rg-aisearch-private-dev` in `westeurope` and uses `config/variables.bicepparam`.

Custom deployment:

```bash
./deploy.sh <resource-group-name> <azure-region> <parameter-file>
```

Example:

```bash
./deploy.sh rg-contoso-ai-search-prod westeurope config/prod.example.bicepparam
```

## File layout

```text
.
├── main.bicep                         # Orchestrates all modules and optional index setup
├── deploy.sh                          # One-command deployment wrapper
├── config/
│   ├── variables.bicepparam           # Main novice-friendly, heavily documented variables file
│   └── prod.example.bicepparam        # Example production-style overrides
├── modules/
│   ├── network.bicep                  # VNet and private endpoint subnet
│   ├── search-service.bicep           # Azure AI Search service
│   ├── private-endpoint.bicep         # Private endpoint and private DNS wiring
│   └── foundry-vnet-peering.bicep     # Optional peering to existing Foundry/BYO VNet
├── scripts/
│   └── create-index.sh                # Deployment script to create/update optional sample index
└── docs/
    ├── architecture.md
    ├── parameters.md
    └── operations.md
```

## How Foundry connectivity is handled

There are two common private networking patterns:

1. **Foundry BYO VNet / Standard Setup with private networking**  
   Set `foundryVnetName`, `foundryVnetResourceGroupName` and `foundryVnetSubscriptionId` in `config/variables.bicepparam`. The template can:
   - link that VNet to the Search private DNS zone;
   - create VNet peering between the Search VNet and Foundry VNet.

2. **Foundry managed virtual network**  
   Foundry managed VNet has its own managed network/outbound private endpoint experience. This template still creates the Search service privately, but you configure the managed VNet private endpoint/outbound rule from the Foundry side.

## Recommended defaults

For a secure dev/demo baseline:

- `publicNetworkAccess = 'Disabled'`
- `searchSkuName = 'standard'`
- `replicaCount = 1`
- `partitionCount = 1`
- `semanticSearch = 'standard'`
- `networkRuleBypass = 'None'`
- `disableLocalAuth = false` for easiest one-command setup; switch to `true` once your app and automation use Microsoft Entra ID/RBAC.

For production, consider:

- 2+ replicas for query availability.
- Separate hub/spoke DNS managed by platform team.
- `disableLocalAuth = true` with RBAC-only access.
- Private endpoint DNS integrated with central DNS forwarding.
- Diagnostic settings and Defender/Sentinel policy from your landing zone.
- CMK only if your customer/regulatory requirement needs it.

## One-command behaviour

`deploy.sh` does two things:

1. Creates the resource group if needed.
2. Runs `az deployment group create` with `main.bicep` and the chosen `.bicepparam` file.

If `createSampleIndex = true`, the Bicep deployment also runs `scripts/create-index.sh` as an Azure deployment script to create/update the sample index.

## Validation commands

After deployment, check outputs:

```bash
az deployment group show \
  --resource-group rg-aisearch-private-dev \
  --name aisearch-private-vnet \
  --query properties.outputs
```

Check private endpoint status:

```bash
az network private-endpoint-connection list \
  --id $(az search service show -g rg-aisearch-private-dev -n <search-name> --query id -o tsv) \
  -o table
```

From a VM or client inside a linked VNet, DNS should resolve to a private IP:

```bash
nslookup <search-name>.search.windows.net
```

## Notes and limitations

- Azure AI Search index creation is a data-plane operation. The core Search service is ARM/Bicep; the optional index is created via an Azure deployment script.
- Private endpoints require Basic tier or higher; this project excludes Free tier intentionally.
- VNet peering requires non-overlapping address spaces and permission on both VNets.
- If your organisation uses central private DNS zones, set `createPrivateDnsZone = false` and let the platform team link/register records.
- This template does not deploy the Foundry account/project itself. It prepares the private Azure AI Search side and lets you parameterise the Foundry VNet when known.

## References

- [Azure AI Search private endpoint documentation](https://learn.microsoft.com/en-us/azure/search/service-create-private-endpoint)
- [Azure AI Search Bicep quickstart](https://learn.microsoft.com/en-us/azure/search/search-get-started-bicep)
- [Microsoft.Search/searchServices ARM/Bicep reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.search/2025-05-01/searchservices)
- [Foundry network isolation documentation](https://learn.microsoft.com/en-us/azure/foundry/how-to/configure-private-link)
- [Foundry Agent Service private networking](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/virtual-networks)


## Developer ingestion lab

I added `sample-ingestion/`, a developer exercise pack with start files and solution files for:

- creating an index from a JSON index array;
- adding, updating and deleting documents;
- exact text and date filters;
- vector search over title and body fields;
- hybrid search with security trimming;
- parsing a Word document and mapping it into the index schema.

Start with `sample-ingestion/README.local.md` for no-Azure exercises, or `sample-ingestion/README.full-azure.md` for the full Azure backend path.


## Developer tooling bootstrap

Install terminal dependencies with:

```bash
./scripts/install-dev-tools.sh
```

Windows PowerShell:

```powershell
.\scripts\install-dev-tools.ps1
```

These scripts install/check common dependencies such as Azure CLI, Bicep, Python, pip, make, git, jq, curl and unzip where supported.


## Azure AI Search deep-dive guides

Additional markdown guides are included in `docs/`:

- `docs/ai-search-index-configuration.md` - explains fields, vectorSearch, HNSW, semantic config, cracking, chunking and enrichment settings.
- `docs/ai-search-search-options.md` - explains keyword, semantic, vector, hybrid and filtered search with Python examples.
- `docs/ai-search-security-trimming-rbac-sensitivity.md` - explains RBAC, ACL trimming, sensitivity filtering and secure RAG retrieval patterns.


## Notebook: integrated vectorisation and custom indexing

See `sample-ingestion/notebooks/integrated-vectorization-custom-indexing.ipynb` for a runnable notebook that demonstrates indexers, skillsets, Azure OpenAI embeddings, mixed file upload, chunking, query examples and chunk diagnostics.
