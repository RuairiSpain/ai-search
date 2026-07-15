# Parameter guide

The main parameter file is `config/variables.bicepparam`. It is intentionally verbose so people can configure the deployment without reading all service docs first.

## Most commonly changed

| Parameter | Default | Change when |
|---|---:|---|
| `location` | `westeurope` | You need a different Azure region. |
| `searchServiceName` | generated | You need a predictable globally unique Search name. |
| `searchSkuName` | `standard` | You want cheaper demo (`basic`) or larger capacity (`standard2`, `standard3`, storage optimised). |
| `replicaCount` | `1` | You need more query throughput or production availability. |
| `partitionCount` | `1` | You need more storage/indexing capacity. |
| `publicNetworkAccess` | `Disabled` | Only set `Enabled` for non-private dev/test scenarios. |
| `disableLocalAuth` | `false` | Set `true` for keyless/RBAC-only production patterns. |
| `foundryVnetName` | empty | Set later when the Foundry BYO VNet name is known. |
| `createSampleIndex` | `true` | Set `false` if you only want infrastructure. |

## AI Search behaviour

- `semanticSearch`: controls semantic ranker availability on the Search service.
- `hostingMode`: almost always `default`.
- `networkRuleBypass`: keep `None` for strict isolation.
- `allowedPublicIpRules`: only useful when public network access is enabled.
- `cmkEnforcement`: keep `Disabled` unless CMK is required and designed.
- `searchIdentityType`: use `SystemAssigned` unless you explicitly do not want a managed identity.

## Index definition

`sampleIndexDefinition` is a full JSON index payload embedded as a Bicep object. Replace it with your real index shape when ready.

Key fields included:

- `id`: document key.
- `title` and `content`: searchable text.
- `source`: filter/facet/citation field.
- `group_ids`: collection field for security trimming filters.
- `contentVector`: vector field.
- `vectorSearch`: HNSW vector configuration.
- `semantic`: semantic configuration.

If you change embedding model dimensions, update `contentVector.dimensions`.
