"""Local Exercise 02 - Exact text/date, vector and hybrid search locally."""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import LocalSearchProvider
from src import local_backend


def require_answer(value, todo: str):
    if value is None:
        raise SystemExit(f"Exercise incomplete: fill {todo}, or uncomment the SOLUTION comment beside it.")
    return value

provider = LocalSearchProvider()
local_backend.seed_sample_data()

# TODO 1: exact metadata category filter.
policy_docs = None  # SOLUTION: policy_docs = provider.exact_category("Policy")
policy_docs = require_answer(policy_docs, "TODO 1")
print("Policy docs:", [d["id"] for d in policy_docs])

# TODO 2: exact date filter using yyyy-mm-dd.
date_docs = None  # SOLUTION: date_docs = provider.exact_published_date("2026-07-01")
date_docs = require_answer(date_docs, "TODO 2")
print("Published on 2026-07-01:", [d["id"] for d in date_docs])

# TODO 3: phrase search.
phrase_docs = None  # SOLUTION: phrase_docs = provider.phrase_search("private endpoint")
phrase_docs = require_answer(phrase_docs, "TODO 3")
print("Phrase search:", [d["id"] for d in phrase_docs])

# TODO 4: local vector-style search.
vector_results = None  # SOLUTION: vector_results = provider.vector_search("secure Azure AI Search private endpoints", top=3)
vector_results = require_answer(vector_results, "TODO 4")
print("Vector-style search:", [(d["id"], d["score"]) for d in vector_results])

# TODO 5: hybrid search with fake security trimming.
hybrid_results = None  # SOLUTION: hybrid_results = provider.hybrid_search("private endpoint DNS for search", caller_groups=["ai-platform", "sre"], top=3)
hybrid_results = require_answer(hybrid_results, "TODO 5")
print("Hybrid search:", [(d["id"], d["score"]) for d in hybrid_results])
