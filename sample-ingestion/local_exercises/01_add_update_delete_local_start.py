"""Local Exercise 01 - Add, update and delete documents using the provider abstraction."""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import LocalSearchProvider
from src import local_backend
from src.data_loader import load_new_document
from src.vector_utils import add_vectors


def require_answer(value, todo: str):
    if value is None:
        raise SystemExit(f"Exercise incomplete: fill {todo}, or uncomment the SOLUTION comment beside it.")
    return value

provider = LocalSearchProvider()
local_backend.seed_sample_data()

# TODO 1: load the new document and add it with merge-or-upload.
new_doc = None  # SOLUTION: new_doc = load_new_document(with_vectors=True)
new_doc = require_answer(new_doc, "TODO 1")
added = None  # SOLUTION: added = provider.merge_or_upload_documents([new_doc])
added = require_answer(added, "TODO 1 upload")
print("Added", added)

patch = {"id": "doc-005", "title": "Developer Quickstart for Document Upload and Updates", "content": "Developers can push and update JSON documents safely.", "lastUpdated": "2026-07-15T00:00:00Z"}
# TODO 2: recalculate vectors on changed title/content and merge the patch.
patch = None  # SOLUTION: patch = add_vectors(patch)
patch = require_answer(patch, "TODO 2 vectors")
updated = None  # SOLUTION: updated = provider.merge_documents([patch])
updated = require_answer(updated, "TODO 2 merge")
print("Updated", updated)

# TODO 3: delete doc-006 by key.
deleted = None  # SOLUTION: deleted = provider.delete_documents(["doc-006"])
deleted = require_answer(deleted, "TODO 3")
print("Deleted", deleted)
