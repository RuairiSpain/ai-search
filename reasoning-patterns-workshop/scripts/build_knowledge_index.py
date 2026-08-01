"""Index the fake policy corpus into Azure AI Search — the knowledge base that
hosted agents attach (Foundry IQ knowledge source / azure_ai_search tool).

Naive chunking (per markdown H2 section) is deliberate: workshop attendees can
see retrieval quality issues in traces and discuss why grounding != reasoning.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from reasoning_common.config import shared_env  # noqa: E402

from azure.identity import DefaultAzureCredential  # noqa: E402
from azure.search.documents import SearchClient  # noqa: E402
from azure.search.documents.indexes import SearchIndexClient  # noqa: E402
from azure.search.documents.indexes.models import (  # noqa: E402
    SearchableField, SearchIndex, SimpleField,
)

INDEX = "contoso-policies"
DOCS = Path(__file__).resolve().parents[1] / "common" / "knowledge" / "documents"


def chunks():
    n = 0
    for f in sorted(DOCS.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        parts = re.split(r"\n(?=## )", text)
        for p in parts:
            n += 1
            yield {"id": f"c{n:04d}", "source": f.name,
                   "title": p.splitlines()[0].lstrip("# ").strip(),
                   "content": p.strip()}


def main():
    env = shared_env()
    cred = DefaultAzureCredential()
    idx_client = SearchIndexClient(env["SEARCH_ENDPOINT"], cred)
    index = SearchIndex(name=INDEX, fields=[
        SimpleField(name="id", type="Edm.String", key=True),
        SearchableField(name="source", type="Edm.String", filterable=True),
        SearchableField(name="title", type="Edm.String"),
        SearchableField(name="content", type="Edm.String"),
    ])
    idx_client.create_or_update_index(index)
    sc = SearchClient(env["SEARCH_ENDPOINT"], INDEX, cred)
    docs = list(chunks())
    sc.merge_or_upload_documents(docs)
    print(f"Indexed {len(docs)} chunks from {len(list(DOCS.glob('*.md')))} documents into '{INDEX}'.")


if __name__ == "__main__":
    main()
