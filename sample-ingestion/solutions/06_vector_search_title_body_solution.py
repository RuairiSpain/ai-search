from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import AzureSearchProvider

provider = AzureSearchProvider()
for r in provider.vector_search("How do I secure Azure AI Search with private endpoints?", top=5):
    print(r["@search.score"], r["id"], r["title"], r["category"])
