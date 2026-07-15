SHELL := /usr/bin/env bash
RESOURCE_GROUP ?= rg-aisearch-private-dev
LOCATION ?= westeurope
PARAM_FILE ?= config/variables.bicepparam
PYTHON ?= python3

.PHONY: help install-tools venv validate-local bicep-build deploy local azure-create-index azure-upload azure-add azure-update azure-delete azure-search-text azure-search-vector azure-search-hybrid azure-parse-word azure-ingest-word azure-real-embeddings azure-schema-v2 clean

help:
	@echo "Targets:"
	@echo "  install-tools        Install CLI and developer dependencies (Linux/macOS bash path)"
	@echo "  venv                 Create Python venv and install sample-ingestion requirements"
	@echo "  validate-local       Run offline validation, no Azure required"
	@echo "  local                Run all local fake-backend exercises"
	@echo "  bicep-build          Compile main.bicep"
	@echo "  deploy               Deploy Bicep to Azure. Override RESOURCE_GROUP, LOCATION, PARAM_FILE"
	@echo "  azure-create-index   Create/update Azure AI Search index from JSON"
	@echo "  azure-upload         Upload sample docs to Azure AI Search"
	@echo "  azure-search-hybrid  Run Azure hybrid search example"
	@echo "  azure-real-embeddings Upload documents with real Azure OpenAI embeddings"
	@echo "  azure-schema-v2      Create and populate a v2 index schema"

install-tools:
	./scripts/install-dev-tools.sh

venv:
	cd sample-ingestion && $(PYTHON) -m venv .venv && . .venv/bin/activate && python -m pip install --upgrade pip && pip install -r requirements.txt

validate-local:
	cd sample-ingestion && $(PYTHON) validate_local.py

local:
	cd sample-ingestion && $(PYTHON) local_solutions/run_all_local_solutions.py

bicep-build:
	az bicep build --file main.bicep

deploy:
	./deploy.sh $(RESOURCE_GROUP) $(LOCATION) $(PARAM_FILE)

azure-create-index:
	cd sample-ingestion && $(PYTHON) solutions/00_create_index_solution.py

azure-upload:
	cd sample-ingestion && $(PYTHON) solutions/01_upload_documents_solution.py

azure-add:
	cd sample-ingestion && $(PYTHON) solutions/02_add_new_document_solution.py

azure-update:
	cd sample-ingestion && $(PYTHON) solutions/03_update_document_solution.py

azure-delete:
	cd sample-ingestion && $(PYTHON) solutions/04_delete_document_solution.py

azure-search-text:
	cd sample-ingestion && $(PYTHON) solutions/05_search_text_date_solution.py

azure-search-vector:
	cd sample-ingestion && $(PYTHON) solutions/06_vector_search_title_body_solution.py

azure-search-hybrid:
	cd sample-ingestion && $(PYTHON) solutions/07_hybrid_search_solution.py

azure-parse-word:
	cd sample-ingestion && $(PYTHON) solutions/08_parse_word_document_solution.py

azure-ingest-word:
	cd sample-ingestion && $(PYTHON) solutions/09_ingest_parsed_word_solution.py

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf sample-ingestion/.local


azure-real-embeddings:
	cd sample-ingestion && $(PYTHON) solutions/10_real_embeddings_solution.py

azure-schema-v2:
	cd sample-ingestion && $(PYTHON) solutions/11_schema_versioning_solution.py
