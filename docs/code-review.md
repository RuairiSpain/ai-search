# Code review notes

## Validation performed

- Python syntax checked with `py_compile` for `sample-ingestion/src`, `sample-ingestion/exercises` and `sample-ingestion/solutions`.
- Word parsing solution executed locally against `sample-ingestion/data/word/sample-private-networking-policy.docx`.
- Added `sample-ingestion/validate_local.py` for repeatable local validation without calling Azure.
- Added `.gitignore` for Python cache files, virtual environments and local `.env` secrets.

## Current caveats

- Bicep compilation was not executed in this container because Azure CLI/Bicep tooling is not installed here.
- The Azure-dependent exercises require a deployed Azure AI Search service and either an admin API key or Entra ID role assignments.
- The demo vector helper is deterministic and intentionally not semantically meaningful. Replace it with the same production embedding model used for query-time vectors before relevance testing.
- Starter exercise files intentionally contain blanks. They compile, but they are expected to need edits before execution.

## Recommended simplifications

1. Keep Bicep infrastructure separate from data-plane indexing labs. Infrastructure and index/data operations have different lifecycles.
2. Promote `data/indexes/sample-indexes.json` as the source-of-truth schema pattern. This is easier for customers to version than editing nested Bicep object parameters.
3. Add a small CI workflow later that runs `python sample-ingestion/validate_local.py` and `az bicep build --file main.bicep`.
4. Replace deterministic vectors with an `EmbeddingProvider` interface so developers can swap fake vectors, Azure OpenAI and integrated vectorisation more cleanly.
5. Add optional REST samples beside SDK samples for customers who want language-agnostic ingestion examples.


## v4 changes applied

- Replaced Azure CLI token shell-out in `src/rest_index.py` with `DefaultAzureCredential().get_token()`.
- Added `src/providers.py` with `SearchProvider`, `LocalSearchProvider` and `AzureSearchProvider`.
- Updated local exercises to use the provider abstraction and helpful TODO failure messages instead of accidental `NoneType` errors.
- Updated Azure solution files to use `AzureSearchProvider`.
- Replaced local hash-vector ranking with dependency-free TF-IDF style scoring for more intuitive offline results.
- Added runtime validation for local provider exact filters, phrase search, vector-style search and hybrid search.
- Added private networking troubleshooting guidance to the full Azure README.
- Added Exercise 10 for real Azure OpenAI embeddings.
- Added Exercise 11 for schema versioning using a v2 index.
- Added REST `.http` examples for language-agnostic customers.
- Updated CI to run local solutions in addition to validation and Bicep build.
