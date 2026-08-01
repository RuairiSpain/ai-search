// Pattern 03 adds one resource: the blob container used for fan-out state
// checkpoints (the third state-sharing mechanism in the workshop).
targetScope = 'resourceGroup'
param storageAccountName string

resource sa 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource blob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: sa
  name: 'default'
}
resource stateContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'p03-state'
}
output container string = stateContainer.name
