"""Local Exercise 00 - Create a local fake index and seed sample documents."""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import LocalSearchProvider, load_sample_index_definition
from src.data_loader import load_sample_documents


def require_answer(value, todo: str):
    if value is None:
        raise SystemExit(f"Exercise incomplete: fill {todo}, or uncomment the SOLUTION comment beside it.")
    return value

provider = LocalSearchProvider()
index = load_sample_index_definition()
provider.create_or_update_index(index)

# TODO 1: load the local sample documents.
documents = None  # SOLUTION: documents = load_sample_documents(with_vectors=True)
documents = require_answer(documents, "TODO 1")

# TODO 2: upload documents into the local fake backend.
results = None  # SOLUTION: results = provider.upload_documents(documents)
results = require_answer(results, "TODO 2")

print(f"Seeded {len(results)} local documents")
