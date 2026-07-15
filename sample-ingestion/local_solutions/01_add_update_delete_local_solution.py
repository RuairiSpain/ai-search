from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import LocalSearchProvider
from src import local_backend
from src.data_loader import load_new_document
from src.vector_utils import add_vectors

provider = LocalSearchProvider()
local_backend.seed_sample_data()
new_doc = load_new_document(with_vectors=True)
print("Added", provider.merge_or_upload_documents([new_doc]))
patch = {"id": "doc-005", "title": "Developer Quickstart for Document Upload and Updates", "content": "Developers can push and update JSON documents safely.", "lastUpdated": "2026-07-15T00:00:00Z"}
patch = add_vectors(patch)
print("Updated", provider.merge_documents([patch]))
print("Deleted", provider.delete_documents(["doc-006"]))
