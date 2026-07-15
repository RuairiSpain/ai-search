# Azure AI Search sample ingestion exercises

This folder contains two tracks:

- **Local only**: learn concepts with fake local data and no Azure dependency. Start with [`README.local.md`](README.local.md).
- **Full Azure**: deploy the Bicep project, verify Azure AI Search in the Azure portal and run exercises against the real Azure backend. Start with [`README.full-azure.md`](README.full-azure.md).

The folder includes:

- sample index schema array in `data/indexes/sample-indexes.json`;
- sample documents in `data/documents/`;
- Word document parsing sample in `data/word/`;
- local fake backend exercises in `local_exercises/` and `local_solutions/`;
- Azure backend exercises in `exercises/` and `solutions/`;
- reusable helpers in `src/`.

Use `make help` from the repository root to see common commands.
