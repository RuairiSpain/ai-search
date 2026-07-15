from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import AzureSearchProvider
from src.vector_utils import add_vectors

provider = AzureSearchProvider()
partial_update = {
    "id": "doc-005",
    "title": "Developer Quickstart for Document Upload and Updates",
    "content": "Developers can push JSON documents using upload, merge, mergeOrUpload and delete. Always check indexing result objects for failures.",
    "lastUpdated": "2026-07-15T00:00:00Z",
}
partial_update = add_vectors(partial_update)
results = provider.merge_documents([partial_update])
for r in results:
    print(r["key"], r["succeeded"])
