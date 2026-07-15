# Full Azure exercises - Bicep deployment plus Azure AI Search backend

Use this path when developers should deploy the Bicep project, verify the Azure AI Search service in the Azure portal, create the index and run the exercises against the real Azure AI Search backend.

## Prerequisites

Install tools from the repository root:

```bash
./scripts/install-dev-tools.sh
```

Windows PowerShell:

```powershell
.\scripts\install-dev-tools.ps1
```

The scripts install or check common terminal dependencies such as Azure CLI, Bicep, Python, pip, make, git, jq, curl and unzip where supported.

## Deploy the Azure infrastructure

From the repository root:

```bash
az login
make deploy RESOURCE_GROUP=rg-aisearch-private-dev LOCATION=westeurope PARAM_FILE=config/variables.bicepparam
```

Or without make:

```bash
./deploy.sh rg-aisearch-private-dev westeurope config/variables.bicepparam
```

## Check the service in the Azure portal

1. Open the Azure portal.
2. Go to the resource group you deployed, for example `rg-aisearch-private-dev`.
3. Open the Azure AI Search service.
4. Confirm the service exists and note the endpoint value.
5. Check **Networking** and confirm public network access/private endpoint settings match your parameter file.
6. Open **Indexes**. If Bicep sample index creation is disabled, the list might be empty until you run the create-index exercise.



## Important private networking note

The Bicep project is secure by default and can disable public network access. If `publicNetworkAccess = 'Disabled'`, data-plane calls from your laptop will only work if your machine can reach the private endpoint through the right network path, such as VPN, ExpressRoute, a jumpbox/VM in the VNet, or a build agent with line-of-sight to the private endpoint.

If you are running a workshop from normal developer laptops, choose one of these options:

1. Temporarily set `publicNetworkAccess = 'Enabled'` and restrict access appropriately for the lab.
2. Run the Python exercises from a VM or dev container inside the linked VNet.
3. Use the local exercise path first, then demo the Azure portal and private networking separately.

If DNS resolves `<search-name>.search.windows.net` to a public IP while public access is disabled, index creation and document upload calls will fail.

## Configure the Python lab

```bash
cd sample-ingestion
cp .env.example .env
```

Edit `.env`:

```bash
SEARCH_ENDPOINT=https://<your-search-service>.search.windows.net
SEARCH_INDEX_NAME=rag-documents-index
SEARCH_API_KEY=<admin-key-for-local-demo>
```

For keyless auth, leave `SEARCH_API_KEY` blank, run `az login`, and assign your identity the required Azure AI Search data-plane roles.

## Run the Azure exercises

From the repository root:

```bash
make azure-create-index
make azure-upload
make azure-add
make azure-update
make azure-search-text
make azure-search-vector
make azure-search-hybrid
make azure-parse-word
make azure-ingest-word
make azure-real-embeddings
make azure-schema-v2
```

Delete exercise:

```bash
make azure-delete
```

## Exercise details

- `solutions/00_create_index_solution.py` creates or updates the index from `data/indexes/sample-indexes.json`.
- `solutions/01_upload_documents_solution.py` uploads the initial sample documents.
- `solutions/02_add_new_document_solution.py` adds one document using merge-or-upload semantics.
- `solutions/03_update_document_solution.py` updates selected fields and recalculates vectors.
- `solutions/04_delete_document_solution.py` deletes a document by key.
- `solutions/05_search_text_date_solution.py` demonstrates exact metadata, date and phrase search.
- `solutions/06_vector_search_title_body_solution.py` searches `titleVector` and `contentVector`.
- `solutions/07_hybrid_search_solution.py` combines keyword, vector, semantic ranking and security trimming.
- `solutions/08_parse_word_document_solution.py` parses a Word document locally.
- `solutions/09_ingest_parsed_word_solution.py` parses and uploads the Word-derived document.

## Recommended exercise flow for workshops

1. Run local exercises first using `README.local.md`.
2. Deploy Azure infrastructure.
3. Check the Search service and networking in the Azure portal.
4. Create the index from JSON.
5. Upload documents.
6. Run exact, vector and hybrid searches.
7. Parse the Word document and ingest it.
8. Discuss how to replace deterministic fake vectors with production embeddings.


## Production realism exercises

- `solutions/10_real_embeddings_solution.py` replaces deterministic demo vectors with Azure OpenAI embeddings. Use this when you want to test retrieval quality rather than just code flow.
- `solutions/11_schema_versioning_solution.py` creates `rag-documents-index-v2`, adds a `businessUnit` field and re-ingests documents. This demonstrates the recommended versioned-index approach for schema evolution.

## REST examples

The `rest/` folder includes language-agnostic `.http` examples for creating an index, uploading documents and shaping a hybrid search request. These are useful for customers who prefer Postman, VS Code REST Client, APIM, Java, .NET or JavaScript over Python.


Note: the REST upload sample intentionally omits vector fields for readability. Use the Python exercises or add correctly dimensioned vectors when testing vector search through REST.


## Integrated vectorisation notebook

A runnable notebook is included at `sample-ingestion/notebooks/integrated-vectorization-custom-indexing.ipynb`. It has separate cells for installing dependencies, uploading PDF/Word/PowerPoint/image files to Blob Storage, creating the index, data source, skillset, indexer, running keyword/vector/hybrid queries and plotting chunk size distribution.
