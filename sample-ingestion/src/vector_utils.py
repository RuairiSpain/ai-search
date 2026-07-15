from __future__ import annotations
import hashlib
import math


def deterministic_embedding(text: str, dimensions: int = 1536) -> list[float]:
    """Return a stable demo embedding.

    This is intentionally not a real semantic embedding. It lets developers run the
    exercises without an Azure OpenAI dependency. Replace this function with the
    same embedding model you use in production before measuring search relevance.
    """
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for byte in digest:
            values.append((byte / 255.0) * 2.0 - 1.0)
            if len(values) == dimensions:
                break
        counter += 1
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [round(v / norm, 6) for v in values]


def add_vectors(document: dict) -> dict:
    """Populate titleVector and contentVector from title and content."""
    enriched = dict(document)
    enriched["titleVector"] = deterministic_embedding(enriched.get("title", ""))
    enriched["contentVector"] = deterministic_embedding(enriched.get("content", ""))
    return enriched
