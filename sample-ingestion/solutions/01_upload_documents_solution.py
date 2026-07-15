from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import AzureSearchProvider
from src.data_loader import load_sample_documents

provider = AzureSearchProvider()
documents = load_sample_documents(with_vectors=True)
results = provider.upload_documents(documents)
for r in results:
    print(r["key"], r["succeeded"])
