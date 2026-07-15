"""Exercise 02 - Add one new document with merge_or_upload_documents.
Run: python exercises/02_add_new_document_start.py
"""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.config import get_search_client
from src.data_loader import load_new_document

client = get_search_client()

# TODO 1: load the new document and populate vector fields.
document = None  # SOLUTION: document = load_new_document(with_vectors=True)

# TODO 2: merge or upload means create if missing, update if present.
results = None  # SOLUTION: results = client.merge_or_upload_documents(documents=[document])

for r in results:
    print(r.key, r.succeeded)
