"""Exercise 07 - Hybrid search: keyword + vector + optional semantic ranking + security trimming.
Run: python exercises/07_hybrid_search_start.py
"""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from azure.search.documents.models import VectorizedQuery
from src.config import get_search_client
from src.vector_utils import deterministic_embedding

client = get_search_client()
query = "private endpoint DNS for search"
caller_groups = ["ai-platform", "sre"]

# TODO 1: vectorise the user query.
query_vector = None  # SOLUTION: query_vector = deterministic_embedding(query)

# TODO 2: create the vector side of the hybrid request.
vector_query = None  # SOLUTION: vector_query = VectorizedQuery(vector=query_vector, k_nearest_neighbors=20, fields="titleVector,contentVector")

# TODO 3: security-trim results to caller groups.
security_filter = None  # SOLUTION: security_filter = "group_ids/any(g: search.in(g, 'ai-platform,sre'))"

# TODO 4: combine keyword search_text and vector_queries in one request.
results = None  # SOLUTION: results = client.search(search_text=query, vector_queries=[vector_query], filter=security_filter, query_type="semantic", semantic_configuration_name="default-semantic-config", select=["id", "title", "category", "sourceFile"], top=5)

for r in results:
    print(r.get("@search.reranker_score", r["@search.score"]), r["id"], r["title"], r["category"], r["sourceFile"])
