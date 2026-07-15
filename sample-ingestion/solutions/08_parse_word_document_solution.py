from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from docx import Document
from src.vector_utils import add_vectors

ROOT = Path(__file__).resolve().parents[1]
DOCX_PATH = ROOT / "data/word/sample-private-networking-policy.docx"


def parse_docx(path: Path) -> dict:
    doc = Document(path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    title = paragraphs[0] if paragraphs else path.stem
    content = "\n".join(paragraphs[1:]) if len(paragraphs) > 1 else title
    search_doc = {
        "id": "word-001",
        "documentCode": "WORD-001",
        "title": title,
        "content": content,
        "category": "Policy",
        "sourceFile": path.name,
        "publishedDate": "2026-07-15T00:00:00Z",
        "lastUpdated": "2026-07-15T00:00:00Z",
        "group_ids": ["ai-platform", "developers"],
    }
    return add_vectors(search_doc)


if __name__ == "__main__":
    parsed = parse_docx(DOCX_PATH)
    for key in ["id", "documentCode", "title", "category", "sourceFile"]:
        print(key, "=", parsed[key])
    print("content preview =", parsed["content"][:250])
    print("titleVector dimensions =", len(parsed["titleVector"]))
