from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import AzureSearchProvider

provider = AzureSearchProvider()
for r in provider.hybrid_search("private endpoint DNS for search", caller_groups=["ai-platform", "sre"], top=5):
    print(r.get("@search.reranker_score", r["@search.score"]), r["id"], r["title"], r["category"], r["sourceFile"])
