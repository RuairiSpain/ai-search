# Local exercises - no Azure required

Use this path when you want developers to learn the ingestion and search concepts without deploying anything to Azure.

The local exercises use:

- JSON sample documents in `data/documents/`.
- The same sample index schema in `data/indexes/sample-indexes.json`.
- A fake local backend in `src/local_backend.py`, accessed through the same `SearchProvider` abstraction used by the Azure exercises.
- Deterministic fake vectors from `src/vector_utils.py`.
- A persisted local state file under `.local/local-index.json`.

This is ideal for workshops, demos and first-run validation because it has no Azure cost and no networking prerequisites.

## Setup

From the repository root:

```bash
make venv
make validate-local
```

Or directly:

```bash
cd sample-ingestion
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python validate_local.py
```

## Run all local solutions

```bash
make local
```

Or:

```bash
cd sample-ingestion
python local_solutions/run_all_local_solutions.py
```

## Do the local exercises manually

Starter files contain blanks. The solution is commented beside each blank, so a developer can uncomment it if they get stuck.

```bash
cd sample-ingestion
python local_exercises/00_seed_local_start.py
python local_exercises/01_add_update_delete_local_start.py
python local_exercises/02_search_local_start.py
```

Then compare with:

```bash
python local_solutions/00_seed_local_solution.py
python local_solutions/01_add_update_delete_local_solution.py
python local_solutions/02_search_local_solution.py
```

## What developers learn locally

1. How documents map to an Azure AI Search-style schema.
2. Why metadata fields need to be filterable for exact filters.
3. Why title/body vectors should be updated when source text changes.
4. How exact search, vector search and hybrid search differ conceptually.
5. How security trimming works at a high level using `group_ids`.
6. How to parse a Word document and map it into a search document shape.

## Local limitations

The fake backend is deliberately simple. It is not a replacement for Azure AI Search. It does not implement real BM25, semantic ranker, RRF, analyzers, scoring profiles, paging or service limits. Use the full Azure README when you want to test the real service.
