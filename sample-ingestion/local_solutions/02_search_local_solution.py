from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import LocalSearchProvider
from src import local_backend

provider = LocalSearchProvider()
local_backend.seed_sample_data()
policy_docs = provider.exact_category("Policy")
print("Policy docs:", [d["id"] for d in policy_docs])
date_docs = provider.exact_published_date("2026-07-01")
print("Published on 2026-07-01:", [d["id"] for d in date_docs])
phrase_docs = provider.phrase_search("private endpoint")
print("Phrase search:", [d["id"] for d in phrase_docs])
vector_results = provider.vector_search("secure Azure AI Search private endpoints", top=3)
print("Vector-style search:", [(d["id"], d["score"]) for d in vector_results])
hybrid_results = provider.hybrid_search("private endpoint DNS for search", caller_groups=["ai-platform", "sre"], top=3)
print("Hybrid search:", [(d["id"], d["score"]) for d in hybrid_results])
