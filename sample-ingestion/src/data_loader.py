from __future__ import annotations
import json
from pathlib import Path
from .vector_utils import add_vectors

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_sample_documents(with_vectors: bool = True) -> list[dict]:
    docs = load_json(ROOT / "data/documents/sample-documents.json")
    return [add_vectors(d) for d in docs] if with_vectors else docs


def load_new_document(with_vectors: bool = True) -> dict:
    doc = load_json(ROOT / "data/documents/new-document.json")
    return add_vectors(doc) if with_vectors else doc
