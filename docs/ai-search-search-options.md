# Azure AI Search search options and example calls

This guide explains the main ways to query Azure AI Search and when to use each one.

## 1. Retrieval pipeline options

Azure AI Search can support several retrieval patterns:

| Search type | What it does | Best for |
|---|---|---|
| Keyword search | Lexical matching over searchable fields | Exact terms, acronyms, error codes, product names |
| Filtered search | Exact filtering over filterable fields | Country, year, department, classification, ACLs |
| Semantic search | Reranks text results for semantic relevance | Natural language questions over rich text |
| Vector search | Finds nearest vectors using embeddings | Meaning-based retrieval |
| Hybrid search | Combines keyword and vector retrieval | Enterprise RAG default |
| Hybrid + semantic | Combines BM25, vectors, fusion and semantic reranking | High-quality RAG with better final ordering |
| Hybrid + security filters | Applies ACL/sensitivity filters before results reach the model | Enterprise-secure agent retrieval |

## 2. Keyword search

Keyword search uses lexical matching and ranking over `searchable` text fields.

```python
results = client.search(
    search_text="private endpoint",
    select=["id", "title", "category"],
    top=5
)

for result in results:
    print(result["id"], result["title"])
```

Use keyword search for:

- exact product names;
- support incidents or error codes;
- customer IDs;
- acronyms;
- policy names;
- named entities.

Example:

```text
SQL72014
GH-300
Private Link
ExpressRoute
```

## 3. Exact filtering

Filters use OData syntax over `filterable` fields.

### Exact text filter

```python
results = client.search(
    search_text="*",
    filter="category eq 'Policy'",
    select=["id", "title", "category"],
    top=10
)
```

### Date filter

Use a half-open range for date equality.

```python
results = client.search(
    search_text="*",
    filter="publishedDate ge 2026-07-01T00:00:00Z and publishedDate lt 2026-07-02T00:00:00Z",
    select=["id", "title", "publishedDate"],
    top=10
)
```

### Collection filter

```python
results = client.search(
    search_text="*",
    filter="tags/any(t: search.in(t, 'networking,security'))",
    select=["id", "title", "tags"],
    top=10
)
```

Use filters when values must be exact. For your use cases, fields such as `year`, `month`, `country`, `market`, `businessUnit`, `classification` and `group_ids` should generally be filters, not just semantic text.

## 4. Phrase search

Use Lucene query syntax for phrase matching.

```python
results = client.search(
    search_text='"private endpoint"',
    query_type="full",
    search_fields=["content"],
    select=["id", "title"],
    top=10
)
```

Use when the exact phrase matters.

## 5. Semantic search

Semantic search reranks an initial result set using semantic ranker.

```python
results = client.search(
    search_text="how do I secure Azure AI Search",
    query_type="semantic",
    semantic_configuration_name="default-semantic-config",
    select=["id", "title", "content"],
    top=5
)

for result in results:
    print(result.get("@search.reranker_score"), result["title"])
```

Semantic search is good when the query and document use different words but have similar meaning.

Example:

```text
Query: how do I secure Azure AI Search?
Document: disable public network access and use private endpoints
```

## 6. Vector search

Vector search compares a query embedding to indexed embedding fields.

```python
from azure.search.documents.models import VectorizedQuery

query_vector = embed("How do I secure Azure AI Search with private endpoints?")

vector_query = VectorizedQuery(
    vector=query_vector,
    k_nearest_neighbors=5,
    fields="titleVector,contentVector"
)

results = client.search(
    search_text=None,
    vector_queries=[vector_query],
    select=["id", "title", "category"],
    top=5
)
```

Use vector search for meaning-based retrieval where exact terms may differ.

## 7. Hybrid search

Hybrid search combines keyword and vector retrieval in a single request.

```python
from azure.search.documents.models import VectorizedQuery

query = "private endpoint DNS for search"
query_vector = embed(query)

vector_query = VectorizedQuery(
    vector=query_vector,
    k_nearest_neighbors=20,
    fields="titleVector,contentVector"
)

results = client.search(
    search_text=query,
    vector_queries=[vector_query],
    select=["id", "title", "category", "sourceFile"],
    top=5
)
```

Conceptually:

```text
Keyword search result list
+
Vector search result list
↓
Fusion/reranking
↓
Final results
```

Hybrid search is usually better than pure vector search for enterprise content because business search often includes both:

- meaning-based questions;
- exact terms, dates, identifiers, SKUs, policy names and acronyms.

## 8. Hybrid + semantic reranking

This is a strong default for RAG.

```python
results = client.search(
    search_text=query,
    vector_queries=[vector_query],
    query_type="semantic",
    semantic_configuration_name="default-semantic-config",
    select=["id", "title", "content", "sourceFile"],
    top=5
)
```

Pipeline:

```text
BM25 keyword search
+
Vector search
↓
Result fusion
↓
Semantic ranker
↓
Top grounded chunks
```

## 9. Hybrid + security trimming

Enterprise pattern:

```python
caller_groups = ["ai-platform", "sre"]
groups_csv = ",".join(caller_groups)

security_filter = f"group_ids/any(g: search.in(g, '{groups_csv}'))"

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

This ensures retrieval only considers documents the user is allowed to see.

## 10. Faceted search

Use facets for UI refiners.

```python
results = client.search(
    search_text="private endpoint",
    facets=["category", "businessUnit", "classification"],
    top=10
)

print(results.get_facets())
```

Useful for user-driven filtering.

## 11. Sorting

```python
results = client.search(
    search_text="private endpoint",
    order_by=["publishedDate desc"],
    select=["id", "title", "publishedDate"],
    top=10
)
```

Use sorting when recency or numeric ranking matters.

## 12. Search fields

Limit keyword search to specific fields.

```python
results = client.search(
    search_text="private endpoint",
    search_fields=["title", "content"],
    select=["id", "title"],
    top=5
)
```

This is useful when metadata fields are searchable but should not dominate a specific query.

## 13. Select fields

Return only what the app needs.

```python
results = client.search(
    search_text=query,
    select=["id", "title", "sourceFile", "content"],
    top=5
)
```

Do not return vector fields unless required.

## 14. Recommended RAG search recipe

For enterprise RAG and agents:

```python
results = client.search(
    search_text=query,
    vector_queries=[vector_query],
    filter=security_filter,
    query_type="semantic",
    semantic_configuration_name="default-semantic-config",
    select=["id", "title", "content", "sourceFile", "classification"],
    top=5
)
```

Recommended pipeline:

```text
User identity
↓
Resolve groups / permissions
↓
Build security filter
↓
Run hybrid query
↓
Semantic rerank
↓
Return only permitted chunks
↓
Ground LLM response
```

## 15. Common tuning guidance

| Issue | Check |
|---|---|
| Exact dates/countries wrong | Use filterable fields, not semantic-only matching |
| Acronyms missed | Add searchable metadata, synonyms or keyword search fields |
| Similar but irrelevant results | Improve chunking, metadata, vector model or semantic config |
| Sensitive docs appearing | Fix ACL filter and sensitivity filtering before LLM call |
| Poor vector relevance | Verify embeddings use same model and dimensions at index/query time |
| Slow vector queries | Tune `efSearch`, reduce `k`, filter earlier or optimise graph settings |
