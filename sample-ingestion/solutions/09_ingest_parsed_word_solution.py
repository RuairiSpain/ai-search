from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.config import get_search_client
from solutions._word_parse_import_helper_solution import parse_docx

ROOT = Path(__file__).resolve().parents[1]
DOCX_PATH = ROOT / "data/word/sample-private-networking-policy.docx"
client = get_search_client()
document = parse_docx(DOCX_PATH)
results = client.merge_or_upload_documents(documents=[document])
for r in results:
    print(r.key, r.succeeded)
