using '../main.bicep'

// ============================================================================
// A2A Gateway deployment parameters
// ============================================================================
// This is the only file most people should edit before their first deploy.
// Field-by-field background: ../../docs/01-gateway-config-and-adapter-contract.md
// ============================================================================

// Azure region. Keep this aligned with the Foundry project's region — see
// docs/05-tier2-hosted-agents.md §6.1 "Region topology".
param location = 'westeurope'

// Short name used to generate resource names. Lowercase letters, numbers and
// hyphens only.
param workloadName = 'a2a-gw'
param environmentName = 'dev'

// --- Auth -------------------------------------------------------------------
// The Entra tenant that issues tokens for your chat client(s).
param gatewayTenantId = '00000000-0000-0000-0000-000000000000'

// The gateway's OWN app registration audience. Chat clients request a token
// scoped to this — never to https://ai.azure.com. Create this app
// registration once, by hand or in a separate identity pipeline; it is not
// something Bicep can create for you (no ARM resource for app registrations).
param gatewayAudience = 'api://a2a-gateway'

// Your own Entra objectId (az ad signed-in-user show --query id -o tsv), so
// you can connect with psql and run migrations. Leave empty in CI.
param extraPostgresAdAdminObjectId = ''
param extraPostgresAdAdminName = ''

// --- Foundry ------------------------------------------------------------
// Provision the Foundry project and its agents separately (docs/04-06,
// azd ai agent init / azd provision). Paste the resulting project endpoint
// here.
param foundryProjectEndpoint = 'https://your-project.services.ai.azure.com/api/projects/your-project'

// --- Sizing -------------------------------------------------------------
// Defaults below are deliberately small/cheap for a dev environment.
param postgresSkuName = 'Standard_B1ms'
param postgresSkuTier = 'Burstable'
param postgresStorageSizeGb = 32

param containerAppCpu = '0.5'
param containerAppMemory = '1.0Gi'
param containerAppMinReplicas = 1
param containerAppMaxReplicas = 3

// Placeholder image until you build and push the real gateway image — see
// ../deploy.sh, which does this for you when you pass --build.
param containerImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
