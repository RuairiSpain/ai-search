"""Exercise 05 - Exact text and date field search.
Run: python exercises/05_search_text_date_start.py
"""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.config import get_search_client

client = get_search_client()

# Scenario A: exact match on a filterable text field.
# TODO 1: filter for category exactly equal to 'Policy'.
category_filter = None  # SOLUTION: category_filter = "category eq 'Policy'"

results = client.search(search_text="*", filter=category_filter, select=["id", "title", "category", "publishedDate"], top=10)
print("\nExact category filter:")
for r in results:
    print(r["id"], r["title"], r["category"], r["publishedDate"])

# Scenario B: exact date match using a half-open range.
# TODO 2: match documents published on 2026-07-01 in UTC.
date_filter = None  # SOLUTION: date_filter = "publishedDate ge 2026-07-01T00:00:00Z and publishedDate lt 2026-07-02T00:00:00Z"

results = client.search(search_text="*", filter=date_filter, select=["id", "title", "publishedDate"], top=10)
print("\nExact published date filter:")
for r in results:
    print(r["id"], r["title"], r["publishedDate"])

# Scenario C: exact phrase query against searchable content.
# TODO 3: use a quoted phrase for full-text phrase matching.
phrase = None  # SOLUTION: phrase = '"private endpoint"'

results = client.search(search_text=phrase, query_type="full", search_fields=["content"], select=["id", "title"], top=10)
print("\nExact phrase search:")
for r in results:
    print(r["id"], r["title"])
