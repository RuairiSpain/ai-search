using '../main.bicep'

// ============================================================================
// Azure AI Search private networking deployment parameters
// ============================================================================
// This is the only file most people should edit.
//
// Goal of this template:
// - Create Azure AI Search with public network access disabled by default.
// - Put Azure AI Search behind a private endpoint in a VNet.
// - Create/link private DNS so clients inside the VNet resolve the Search endpoint
//   to a private IP address.
// - Let you later provide a Foundry/BYO VNet name so Foundry agent networking can
//   resolve and route to the private Search endpoint.
// - Optionally create a small sample index so the deployment proves Search is usable.
//
// Important novice notes:
// - Private endpoint = private inbound path to Azure AI Search.
// - Private DNS = makes <name>.search.windows.net resolve to the private endpoint.
// - VNet peering = allows two VNets to route to each other, if their address spaces
//   do not overlap.
// - Azure AI Search firewall IP rules only matter for public access. With public
//   network access disabled, the private endpoint is the intended path.
// - If Foundry uses managed VNet, you normally configure Foundry managed network
//   private endpoints/outbound rules in Foundry. This template prepares the Search
//   side and DNS/VNet integration for BYO VNet scenarios.
// ============================================================================

// ----------------------------------------------------------------------------
// 1. General deployment naming and tagging
// ----------------------------------------------------------------------------

// Azure region. Keep all connected private networking resources in compatible
// regions. For Spain-based demos, 'spaincentral' is often a good starting point
// if your required services are available there. 'westeurope' is commonly used
// for broader Azure capacity.
param location = 'westeurope'

// Short name used to generate default VNet/private endpoint names. Use lowercase
// letters, numbers and hyphens. Avoid underscores and spaces.
param workloadName = 'aisearch-rag'

// Environment label for names and tags. Examples: dev, test, prod, poc.
param environmentName = 'dev'

// Tags help FinOps, ownership and cleanup. Add anything your customer/platform
// team requires such as costCentre, owner, dataClassification, projectCode.
param tags = {
  workload: 'aisearch-rag'
  environment: 'dev'
  owner: 'cloud-solution-architecture'
  dataClassification: 'demo-or-internal'
  deployedBy: 'bicep'
}

// ----------------------------------------------------------------------------
// 2. Azure AI Search core service settings
// ----------------------------------------------------------------------------

// Globally unique Search service name. If left empty, the template generates one.
// Naming rules: lowercase letters, digits, hyphens; 2-60 chars; no consecutive
// hyphens; cannot start/end with hyphen. Example: 'contoso-rag-search-dev'.
param searchServiceName = ''

// Pricing tier. Private endpoints are not supported on Free, so Free is excluded.
// Quick guidance:
// - basic: cheapest private endpoint-capable tier, good for small demos.
// - standard: sensible default for RAG demos and moderate workloads.
// - standard2/standard3: higher scale/capacity.
// - storage_optimized_l1/l2: large storage-heavy indexes.
param searchSkuName = 'standard'

// Replicas serve query traffic and provide query availability.
// Novice default: 1 for dev/test.
// Production guidance: use 2+ replicas for higher query availability; use 3+ when
// you need both indexing and query workloads with stronger availability posture.
param replicaCount = 1

// Partitions provide storage and indexing capacity.
// Novice default: 1.
// Increase when you have large indexes, heavy indexing, or need more storage.
param partitionCount = 1

// Hosting mode.
// - default: use this almost always.
// - highDensity: specialised scenario for supported S3 services with many small
//   indexes; do not use unless you know you need it.
param hostingMode = 'default'

// Semantic search/ranker setting.
// - standard: enables semantic ranking where supported by your SKU/region.
// - disabled: turn it off.
// - free: limited/free semantic capacity where available.
// For RAG demos, standard is usually the useful default.
param semanticSearch = 'standard'

// Public network access.
// - Disabled: private endpoint required. Recommended default for secure demos.
// - Enabled: public endpoint accepts traffic subject to auth and network rules.
param publicNetworkAccess = 'Disabled'

// Local auth means API keys.
// - false: keeps query/admin key flows available and makes one-command sample
//   index setup easier.
// - true: disables API keys; use Microsoft Entra ID/RBAC only. Stronger for
//   production, but make sure your apps and deployment automation use RBAC.
param disableLocalAuth = false

// If a request presents a bad/missing Entra token, this controls the data-plane
// failure style when API keys are also allowed.
// - http401WithBearerChallenge: useful for clients that need a token challenge.
// - http403: generic forbidden response.
param aadAuthFailureMode = 'http401WithBearerChallenge'

// Network bypass for trusted Azure services.
// - None: strictest; recommended default.
// - AzureServices: allows selected trusted Azure service traffic. Use only when
//   a documented dependency requires it and you accept the broader trust boundary.
param networkRuleBypass = 'None'

// Public IP allow list. Usually keep empty when publicNetworkAccess = Disabled.
// Example if public access is enabled for a corporate NAT address:
// param allowedPublicIpRules = [ '203.0.113.10/32' ]
param allowedPublicIpRules = []

// Customer-managed key enforcement.
// - Disabled: Microsoft-managed keys; easiest and sensible for most demos.
// - Enabled: require CMK configuration/supporting Key Vault/identity pattern.
// - Unspecified: defer explicit enforcement choice.
param cmkEnforcement = 'Disabled'

// Data exfiltration protections. Leave empty unless your target API version and
// workload have a specific documented value you need. This is exposed here so
// advanced users are not blocked by the template.
param dataExfiltrationProtections = []

// Managed identity for the Search service.
// - SystemAssigned: recommended default; useful for CMK and service-to-service.
// - None: no managed identity.
param searchIdentityType = 'SystemAssigned'

// ----------------------------------------------------------------------------
// 3. Search private endpoint VNet
// ----------------------------------------------------------------------------

// VNet that will contain the private endpoint for Azure AI Search.
param searchVnetName = 'vnet-aisearch-rag-dev'

// Address space for the Search VNet. Make sure it DOES NOT overlap with Foundry
// VNet, hub VNets, VPN/ExpressRoute address ranges, or on-prem networks.
param searchVnetAddressPrefixes = [
  '10.70.0.0/24'
]

// Subnet for private endpoints only. A /27 is enough for many demo private
// endpoints. For larger landing zones, allocate more space.
param privateEndpointSubnetName = 'snet-private-endpoints'
param privateEndpointSubnetPrefix = '10.70.0.0/27'

// Private endpoint resource name.
param searchPrivateEndpointName = 'pe-aisearch-rag-dev'

// Azure AI Search private DNS zone. For public Azure, this should normally stay
// as privatelink.search.windows.net.
param privateDnsZoneName = 'privatelink.search.windows.net'

// Create the private DNS zone. Set false if your platform team already manages
// central private DNS zones and will link/register records separately.
param createPrivateDnsZone = true

// Link the Search VNet to the private DNS zone so resources inside the Search VNet
// can resolve the Search endpoint privately.
param linkSearchVnetToPrivateDns = true

// ----------------------------------------------------------------------------
// 4. Foundry VNet placeholder: set later when you know the Foundry VNet
// ----------------------------------------------------------------------------

// Existing Foundry/BYO VNet name. Leave empty for now. Later, set this to the VNet
// used by Foundry Agent Standard Setup with private networking/BYO VNet.
// Example: param foundryVnetName = 'vnet-foundry-prod'
param foundryVnetName = ''

// Resource group/subscription for the Foundry VNet. Defaults in main.bicep point
// to the current resource group/subscription; set these if Foundry VNet lives in
// a network/platform resource group or another subscription.
// param foundryVnetResourceGroupName = 'rg-foundry-network-dev'
// param foundryVnetSubscriptionId = '<subscription-id>'

// Link Foundry VNet to the Search private DNS zone. This is important: without
// DNS, Foundry clients may still resolve the public Search endpoint even though
// the private endpoint exists.
param linkFoundryVnetToPrivateDns = true

// Peer the Search VNet and Foundry VNet. This is useful if the private endpoint is
// in the Search VNet and Foundry compute is in a separate BYO VNet.
// Requirements:
// - Address spaces must not overlap.
// - You need permission to create peering on both VNets.
// - In hub-spoke environments, your platform team may prefer hub routing instead.
param createFoundryVnetPeering = true

// Forwarded traffic is often required if traffic crosses through appliances or
// hub-spoke routing. Keep true for most enterprise topologies.
param allowForwardedTraffic = true

// Gateway settings. Keep these false unless your network team tells you otherwise.
param allowGatewayTransitFromSearchToFoundry = false
param useRemoteGatewaysOnSearchToFoundry = false

// ----------------------------------------------------------------------------
// 5. Sample index setup
// ----------------------------------------------------------------------------

// Create a small example index after deployment. This proves the service is up and
// gives novices a ready index shape: text fields, security-trimming field, vector
// field, vector profile and semantic configuration.
param createSampleIndex = true

// Name of sample index.
param sampleIndexName = 'documents-index'

// Search data-plane API version used by the deployment script to create the index.
// Only change if you need a feature from a specific API version.
param searchDataPlaneApiVersion = '2024-07-01'

// Sample index definition. Novice guide:
// - id: unique key for each document.
// - title/content: searchable text.
// - source: useful for filters/facets/citations.
// - group_ids: security trimming. Your app can filter by caller group IDs.
// - contentVector: embedding vector field. Dimensions must match your embedding
//   model, e.g. 1536 for text-embedding-3-small, 3072 for text-embedding-3-large
//   depending on your chosen dimensions.
// - vectorSearch: HNSW configuration for approximate nearest neighbour search.
// - semantic: prioritises title/content/source for semantic reranking.
param sampleIndexDefinition = {
  name: 'documents-index'
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
