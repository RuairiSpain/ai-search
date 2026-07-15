"""Exercise 11 - Schema versioning with a v2 index.

This creates a new index named rag-documents-index-v2 with an extra businessUnit
field, then re-ingests documents. This is safer than trying to mutate major index
schema decisions in-place.
Run: python exercises/11_schema_versioning_start.py
"""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.data_loader import load_json, load_sample_documents
from src.providers import AzureSearchProvider

ROOT = Path(__file__).resolve().parents[1]

# TODO 1: load data/indexes/sample-indexes-v2.json and select the first index definition.
index_v2 = None  # SOLUTION: index_v2 = load_json(ROOT / "data/indexes/sample-indexes-v2.json")[0]
if index_v2 is None:
    raise SystemExit("Exercise incomplete: fill TODO 1 or uncomment the SOLUTION comment.")

provider = AzureSearchProvider(index_name=index_v2["name"])
provider.create_or_update_index(index_v2)

documents = load_sample_documents(with_vectors=True)
for doc in documents:
    # TODO 2: populate the new v2 field.
    doc["businessUnit"] = None  # SOLUTION: doc["businessUnit"] = "AI Platform"
    if doc["businessUnit"] is None:
        raise SystemExit("Exercise incomplete: fill TODO 2 or uncomment the SOLUTION comment.")

results = provider.upload_documents(documents)
for r in results:
    print(r["key"], r["succeeded"])
print("Created and populated v2 index:", index_v2["name"])
