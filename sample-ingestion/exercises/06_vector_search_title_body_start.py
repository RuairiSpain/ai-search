"""Exercise 06 - Vector search over title and document body vector fields.
Run: python exercises/06_vector_search_title_body_start.py
"""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from azure.search.documents.models import VectorizedQuery
from src.config import get_search_client
from src.vector_utils import deterministic_embedding

client = get_search_client()
query = "How do I secure Azure AI Search with private endpoints?"

# TODO 1: vectorise the query with the same embedding function/model used at ingestion time.
query_vector = None  # SOLUTION: query_vector = deterministic_embedding(query)

# TODO 2: search both titleVector and contentVector.
vector_query = None  # SOLUTION: vector_query = VectorizedQuery(vector=query_vector, k_nearest_neighbors=5, fields="titleVector,contentVector")

results = client.search(search_text=None, vector_queries=[vector_query], select=["id", "title", "category"], top=5)
for r in results:
    print(r["@search.score"], r["id"], r["title"], r["category"])
