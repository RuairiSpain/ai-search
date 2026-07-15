# RBAC, security trimming and sensitivity filtering in Azure AI Search

This guide explains how to design Azure AI Search indexes and queries so users only retrieve documents they are allowed to see.

## 1. Why trimming matters

In enterprise RAG and agent scenarios, security is not just about protecting the Search service endpoint. You must also protect the retrieval result set.

Bad pattern:

```text
User asks question
↓
Search retrieves documents from all departments
↓
LLM sees confidential content
↓
Answer leaks data
```

Good pattern:

```text
User asks question
↓
Resolve user permissions
↓
Apply ACL and sensitivity filters
↓
Search retrieves only permitted documents
↓
LLM receives only permitted grounding data
```

## 2. Platform RBAC vs document-level trimming

There are two different layers.

### Platform RBAC

Controls who can manage or query the Azure AI Search service.

Examples:

- who can create indexes;
- who can upload documents;
- who can query indexes;
- whether API keys or Microsoft Entra ID are used.

Platform RBAC does **not automatically** mean the user can only see document-level content they are allowed to see.

### Document-level security trimming

Controls which documents are returned by a query.

This is usually implemented using filterable fields such as:

```json
{
  "name": "group_ids",
  "type": "Collection(Edm.String)",
  "filterable": true,
  "facetable": true,
  "retrievable": false
}
```

Each document stores the groups or ACLs allowed to access it.

```json
{
  "id": "doc-001",
  "title": "Private Networking Policy",
  "group_ids": ["ai-platform", "security"]
}
```

## 3. Basic group-based trimming

User belongs to:

```python
caller_groups = ["ai-platform", "sre"]
```

Build filter:

```python
groups_csv = ",".join(caller_groups)
security_filter = f"group_ids/any(g: search.in(g, '{groups_csv}'))"
```

Use filter in every query:

```python
results = client.search(
    search_text=query,
    vector_queries=[vector_query],
    filter=security_filter,
    query_type="semantic",
    semantic_configuration_name="default-semantic-config",
    select=["id", "title", "content", "sourceFile"],
    top=5
)
```

Only documents matching at least one caller group are candidates for retrieval.

## 4. User-specific ACLs

Some systems need user-level access as well as group access.

Index fields:

```json
{
  "name": "allowed_users",
  "type": "Collection(Edm.String)",
  "filterable": true,
  "retrievable": false
},
{
  "name": "group_ids",
  "type": "Collection(Edm.String)",
  "filterable": true,
  "retrievable": false
}
```

Filter:

```python
user_id = "user@contoso.com"
groups_csv = "ai-platform,sre"

security_filter = (
    f"allowed_users/any(u: u eq '{user_id}') "
    f"or group_ids/any(g: search.in(g, '{groups_csv}'))"
)
```

## 5. Deny lists

In some systems, deny rules override allow rules.

Index field:

```json
{
  "name": "denied_users",
  "type": "Collection(Edm.String)",
  "filterable": true,
  "retrievable": false
}
```

Filter:

```python
security_filter = (
    f"(allowed_users/any(u: u eq '{user_id}') "
    f"or group_ids/any(g: search.in(g, '{groups_csv}'))) "
    f"and not denied_users/any(u: u eq '{user_id}')"
)
```

Keep the rules simple if possible. Complex ACL logic is harder to test and troubleshoot.

## 6. Sensitivity fields

Add sensitivity metadata during ingestion.

```json
{
  "classification": "Confidential",
  "sensitivityLabel": "HighlyConfidential",
  "isSensitive": true
}
```

Recommended fields:

| Field | Type | Purpose |
|---|---|---|
| `classification` | `Edm.String` | Public, Internal, Confidential, HighlyConfidential |
| `sensitivityLabel` | `Edm.String` | Label name from source system or MIP/Purview-style label |
| `isSensitive` | `Edm.Boolean` | Simple allow/block flag |
| `allowed_roles` | `Collection(Edm.String)` | Roles allowed to access sensitive docs |
| `sourceSystem` | `Edm.String` | SharePoint, Blob, Dataverse, FileShare |
| `retentionCategory` | `Edm.String` | Optional governance metadata |

## 7. Sensitivity trimming examples

### Exclude highly confidential documents

```python
sensitivity_filter = "classification ne 'HighlyConfidential'"
```

### Only public and internal documents

```python
sensitivity_filter = "search.in(classification, 'Public,Internal')"
```

### Combine RBAC and sensitivity

```python
combined_filter = (
    "group_ids/any(g: search.in(g, 'ai-platform,sre')) "
    "and classification ne 'HighlyConfidential'"
)
```

### Allow sensitive documents only for privileged role

```python
if "sensitive-readers" in caller_groups:
    sensitivity_filter = "classification ne null"
else:
    sensitivity_filter = "classification ne 'HighlyConfidential'"
```

## 8. Security trimming in hybrid search

Apply filters as part of the search request.

```python
results = client.search(
    search_text=query,
    vector_queries=[vector_query],
    filter=combined_filter,
    query_type="semantic",
    semantic_configuration_name="default-semantic-config",
    select=["id", "title", "content", "sourceFile", "classification"],
    top=5
)
```

The filter should be applied before results are passed to the LLM or agent.

## 9. Where to get permissions

Common sources:

- Microsoft Entra ID group membership;
- SharePoint item permissions;
- Dataverse row security;
- file share ACLs;
- application-specific roles;
- sensitivity or retention labels from source metadata.

In ingestion, normalise them into search-friendly fields:

```json
{
  "group_ids": ["aad-group-id-1", "aad-group-id-2"],
  "allowed_users": ["user@contoso.com"],
  "classification": "Internal",
  "sensitivityLabel": "General"
}
```

## 10. Important design rule

Do not rely on the LLM to decide whether a user should see a document.

The LLM should only receive permitted content.

Correct order:

```text
Authenticate user
↓
Resolve groups / claims
↓
Build filters
↓
Search with filters
↓
Return only allowed chunks
↓
Generate answer
```

Incorrect order:

```text
Search everything
↓
Send all results to LLM
↓
Ask LLM to hide sensitive content
```

## 11. Recommended secure RAG index fields

```json
{
  "name": "group_ids",
  "type": "Collection(Edm.String)",
  "filterable": true,
  "retrievable": false
},
{
  "name": "allowed_users",
  "type": "Collection(Edm.String)",
  "filterable": true,
  "retrievable": false
},
{
  "name": "classification",
  "type": "Edm.String",
  "filterable": true,
  "facetable": true,
  "retrievable": true
},
{
  "name": "sensitivityLabel",
  "type": "Edm.String",
  "filterable": true,
  "facetable": true,
  "retrievable": true
},
{
  "name": "sourceSystem",
  "type": "Edm.String",
  "filterable": true,
  "facetable": true,
  "retrievable": true
}
```

## 12. Operational checklist

Before production:

- Confirm every query path applies the security filter.
- Unit test filter generation for different user groups.
- Test users with no access, partial access and privileged access.
- Do not return ACL fields unless needed for debugging.
- Log query metadata, not sensitive content.
- Validate that cached answers do not bypass trimming.
- Use managed identity or Entra ID where possible for service access.
- Keep public network access disabled when private networking is required.
- Treat security trimming as mandatory for agentic RAG.
