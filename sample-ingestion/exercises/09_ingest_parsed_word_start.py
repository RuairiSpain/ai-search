"""Bonus Exercise 09 - Parse a Word document and upload it to Azure AI Search.
Run: python exercises/09_ingest_parsed_word_start.py
"""
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.config import get_search_client
from exercises import _word_parse_import_helper

ROOT = Path(__file__).resolve().parents[1]
DOCX_PATH = ROOT / "data/word/sample-private-networking-policy.docx"
client = get_search_client()

# TODO 1: parse the Word document into the index schema.
document = None  # SOLUTION: document = _word_parse_import_helper.parse_docx(DOCX_PATH)

# TODO 2: upload or update the parsed document.
results = None  # SOLUTION: results = client.merge_or_upload_documents(documents=[document])

for r in results:
    print(r.key, r.succeeded)
