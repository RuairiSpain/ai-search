from pathlib import Path
from docx import Document
from src.vector_utils import add_vectors


def parse_docx(path: Path) -> dict:
    doc = Document(path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    title = paragraphs[0] if paragraphs else path.stem
    content = "\n".join(paragraphs[1:]) if len(paragraphs) > 1 else title
    return add_vectors({
        "id": "word-001",
        "documentCode": "WORD-001",
        "title": title,
        "content": content,
        "category": "Policy",
        "sourceFile": path.name,
        "publishedDate": "2026-07-15T00:00:00Z",
        "lastUpdated": "2026-07-15T00:00:00Z",
        "group_ids": ["ai-platform", "developers"],
    })
