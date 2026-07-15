from __future__ import annotations
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Iterable
from .data_loader import load_sample_documents, load_new_document
from .vector_utils import deterministic_embedding

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".local"
STATE_FILE = STATE_DIR / "local-index.json"
TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "")]


def _tfidf_like_score(query: str, document_text: str, corpus: list[str]) -> float:
    """Small offline TF-IDF style score for local exercises.

    This is intentionally simple, dependency-free and explainable. It gives more
    intuitive local results than hash-based fake vectors while still requiring no
    Azure OpenAI deployment.
    """
    query_terms = _tokens(query)
    if not query_terms:
        return 0.0
    doc_terms = _tokens(document_text)
    if not doc_terms:
        return 0.0
    doc_tf = Counter(doc_terms)
    corpus_terms = [set(_tokens(text)) for text in corpus]
    n_docs = max(len(corpus_terms), 1)
    score = 0.0
    for term in query_terms:
        df = sum(1 for terms in corpus_terms if term in terms)
        idf = math.log((1 + n_docs) / (1 + df)) + 1.0
        score += doc_tf.get(term, 0) * idf
    norm = math.sqrt(sum(v * v for v in doc_tf.values())) or 1.0
    return score / norm


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"documents": []}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset_local_index() -> dict:
    state = {"documents": []}
    _save_state(state)
    return state


def upload_documents(documents: list[dict]) -> list[dict]:
    state = _load_state()
    existing = {doc["id"]: doc for doc in state.get("documents", [])}
    for doc in documents:
        existing[doc["id"]] = doc
    state["documents"] = list(existing.values())
    _save_state(state)
    return [{"key": doc["id"], "succeeded": True} for doc in documents]


def merge_or_upload_documents(documents: list[dict]) -> list[dict]:
    return upload_documents(documents)


def merge_documents(documents: list[dict]) -> list[dict]:
    state = _load_state()
    existing = {doc["id"]: doc for doc in state.get("documents", [])}
    results = []
    for patch in documents:
        key = patch["id"]
        if key not in existing:
            results.append({"key": key, "succeeded": False, "error": "Document not found"})
            continue
        existing[key].update(patch)
        results.append({"key": key, "succeeded": True})
    state["documents"] = list(existing.values())
    _save_state(state)
    return results


def delete_documents(keys: Iterable[str]) -> list[dict]:
    delete_set = set(keys)
    state = _load_state()
    state["documents"] = [doc for doc in state.get("documents", []) if doc["id"] not in delete_set]
    _save_state(state)
    return [{"key": key, "succeeded": True} for key in delete_set]


def all_documents() -> list[dict]:
    return _load_state().get("documents", [])


def filter_category(category: str) -> list[dict]:
    return [doc for doc in all_documents() if doc.get("category") == category]


def filter_published_date(date_prefix: str) -> list[dict]:
    return [doc for doc in all_documents() if doc.get("publishedDate", "").startswith(date_prefix)]


def phrase_search(phrase: str, field: str = "content") -> list[dict]:
    phrase_lower = phrase.lower().strip('"')
    return [doc for doc in all_documents() if phrase_lower in doc.get(field, "").lower()]


def vector_search(query: str, fields: str = "titleVector,contentVector", top: int = 5) -> list[dict]:
    """Local stand-in for vector search using TF-IDF style scoring over title/content.

    Azure vector search occurs over vector fields. Locally, we score equivalent
    source text fields so the ranking is human-understandable in a workshop.
    """
    docs = all_documents()
    corpus = [f"{doc.get('title', '')} {doc.get('content', '')}" for doc in docs]
    scored = []
    for doc, text in zip(docs, corpus):
        score = _tfidf_like_score(query, text, corpus)
        scored.append({"score": round(score, 6), **doc})
    return sorted(scored, key=lambda d: d["score"], reverse=True)[:top]


def hybrid_search(query: str, caller_groups: list[str], top: int = 5) -> list[dict]:
    docs = [doc for doc in all_documents() if set(doc.get("group_ids", [])) & set(caller_groups)]
    corpus = [f"{doc.get('title', '')} {doc.get('content', '')}" for doc in docs]
    scored = []
    query_terms = set(_tokens(query))
    for doc, text in zip(docs, corpus):
        text_terms = set(_tokens(text))
        keyword_score = len(query_terms & text_terms) / max(len(query_terms), 1)
        semantic_like_score = _tfidf_like_score(query, text, corpus)
        final_score = (0.45 * keyword_score) + (0.55 * semantic_like_score)
        scored.append({"score": round(final_score, 6), "keyword_score": round(keyword_score, 6), "vector_score": round(semantic_like_score, 6), **doc})
    return sorted(scored, key=lambda d: d["score"], reverse=True)[:top]


def seed_sample_data() -> list[dict]:
    reset_local_index()
    docs = load_sample_documents(with_vectors=True)
    upload_documents(docs)
    return docs


def add_new_sample_document() -> dict:
    doc = load_new_document(with_vectors=True)
    merge_or_upload_documents([doc])
    return doc
