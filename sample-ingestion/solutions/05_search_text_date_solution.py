from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import AzureSearchProvider

provider = AzureSearchProvider()
print("\nExact category filter:")
for r in provider.exact_category("Policy"):
    print(r["id"], r["title"], r["category"], r["publishedDate"])
print("\nExact published date filter:")
for r in provider.exact_published_date("2026-07-01"):
    print(r["id"], r["title"], r["publishedDate"])
print("\nExact phrase search:")
for r in provider.phrase_search("private endpoint"):
    print(r["id"], r["title"])
