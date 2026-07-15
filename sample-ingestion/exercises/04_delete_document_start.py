"""Exercise 04 - Delete a document by key.
Run: python exercises/04_delete_document_start.py
"""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.config import get_search_client

client = get_search_client()

# TODO 1: delete doc-006 by key. Only the key field is required.
results = None  # SOLUTION: results = client.delete_documents(documents=[{"id": "doc-006"}])

for r in results:
    print(r.key, r.succeeded)
