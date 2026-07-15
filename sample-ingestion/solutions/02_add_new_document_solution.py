from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import AzureSearchProvider
from src.data_loader import load_new_document

provider = AzureSearchProvider()
document = load_new_document(with_vectors=True)
results = provider.merge_or_upload_documents([document])
for r in results:
    print(r["key"], r["succeeded"])
