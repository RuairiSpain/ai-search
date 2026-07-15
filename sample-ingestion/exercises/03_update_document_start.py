"""Exercise 03 - Update selected fields on an existing document.
Run: python exercises/03_update_document_start.py
"""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.config import get_search_client
from src.vector_utils import add_vectors

client = get_search_client()

partial_update = {
    "id": "doc-005",
    "title": "Developer Quickstart for Document Upload and Updates",
    "content": "Developers can push JSON documents using upload, merge, mergeOrUpload and delete. Always check indexing result objects for failures.",
    "lastUpdated": "2026-07-15T00:00:00Z",
}

# TODO 1: recalculate vectors because title/content changed.
partial_update = None  # SOLUTION: partial_update = add_vectors(partial_update)

# TODO 2: merge updates only the supplied fields into the existing document.
results = None  # SOLUTION: results = client.merge_documents(documents=[partial_update])

for r in results:
    print(r.key, r.succeeded)
