// User-assigned managed identity for the gateway. Created as its own
// resource (rather than system-assigned on the Container App) so the
// principal id is known before the Container App exists and can be used
// both for the RBAC role assignments in this deployment and for the
// per-agent UserIdentityImpersonation grant done later by
// scripts/grant-agent-access.sh (docs/05-tier2-hosted-agents.md §3,
// docs/02-decisions.md D6 rule L014).
param location string
param tags object
param name string

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

output identityId string = identity.id
output principalId string = identity.properties.principalId
output clientId string = identity.properties.clientId
