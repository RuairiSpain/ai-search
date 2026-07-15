"""Exercise 00 - Create or update the sample Azure AI Search index.

Fill the blanks. The answer is commented beside each blank if you want to move fast.
Run: python exercises/00_create_index_start.py
"""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.data_loader import load_json
from src.rest_index import create_or_update_index

ROOT = Path(__file__).resolve().parents[1]

# TODO 1: load the sample index array from data/indexes/sample-indexes.json.
indexes = None  # SOLUTION: indexes = load_json(ROOT / "data/indexes/sample-indexes.json")

# TODO 2: create or update the first index definition.
result = None  # SOLUTION: result = create_or_update_index(indexes[0])

print(f"Created or updated index: {result['name']}")
