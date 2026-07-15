from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.data_loader import load_json, load_sample_documents
from src.providers import AzureSearchProvider

ROOT = Path(__file__).resolve().parents[1]
index_v2 = load_json(ROOT / "data/indexes/sample-indexes-v2.json")[0]
provider = AzureSearchProvider(index_name=index_v2["name"])
provider.create_or_update_index(index_v2)
documents = load_sample_documents(with_vectors=True)
for doc in documents:
    doc["businessUnit"] = "AI Platform"
results = provider.upload_documents(documents)
for r in results:
    print(r["key"], r["succeeded"])
print("Created and populated v2 index:", index_v2["name"])
