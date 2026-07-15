"""Exercise 01 - Add documents to the index with upload_documents.
Run: python exercises/01_upload_documents_start.py
"""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.config import get_search_client
from src.data_loader import load_sample_documents

client = get_search_client()

# TODO 1: load sample docs with vectors populated.
documents = None  # SOLUTION: documents = load_sample_documents(with_vectors=True)

# TODO 2: upload the whole batch to Azure AI Search.
results = None  # SOLUTION: results = client.upload_documents(documents=documents)

for r in results:
    print(r.key, r.succeeded)
