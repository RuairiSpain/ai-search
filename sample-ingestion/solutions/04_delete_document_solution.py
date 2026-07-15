from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import AzureSearchProvider

provider = AzureSearchProvider()
results = provider.delete_documents(["doc-006"])
for r in results:
    print(r["key"], r["succeeded"])
