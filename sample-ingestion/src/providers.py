from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from . import local_backend
from .data_loader import load_json
from .vector_utils import deterministic_embedding

ROOT = Path(__file__).resolve().parents[1]


class SearchProvider(ABC):
    """Common interface used by workshop exercises for local and Azure backends."""

    @abstractmethod
    def create_or_update_index(self, index_definition: dict) -> dict: ...

    @abstractmethod
    def upload_documents(self, documents: list[dict]) -> list[dict]: ...

    @abstractmethod
    def merge_or_upload_documents(self, documents: list[dict]) -> list[dict]: ...

    @abstractmethod
    def merge_documents(self, documents: list[dict]) -> list[dict]: ...

    @abstractmethod
    def delete_documents(self, keys: list[str]) -> list[dict]: ...

    @abstractmethod
    def exact_category(self, category: str) -> list[dict]: ...

    @abstractmethod
    def exact_published_date(self, yyyy_mm_dd: str) -> list[dict]: ...

    @abstractmethod
    def phrase_search(self, phrase: str) -> list[dict]: ...

    @abstractmethod
    def vector_search(self, query: str, top: int = 5) -> list[dict]: ...

    @abstractmethod
    def hybrid_search(self, query: str, caller_groups: list[str], top: int = 5) -> list[dict]: ...


class LocalSearchProvider(SearchProvider):
    """Offline provider. No Azure dependency."""

    def create_or_update_index(self, index_definition: dict) -> dict:
        local_backend.reset_local_index()
        return {"name": index_definition["name"], "backend": "local"}

    def upload_documents(self, documents: list[dict]) -> list[dict]:
        return local_backend.upload_documents(documents)

    def merge_or_upload_documents(self, documents: list[dict]) -> list[dict]:
        return local_backend.merge_or_upload_documents(documents)

    def merge_documents(self, documents: list[dict]) -> list[dict]:
        return local_backend.merge_documents(documents)

    def delete_documents(self, keys: list[str]) -> list[dict]:
        return local_backend.delete_documents(keys)

    def exact_category(self, category: str) -> list[dict]:
        return local_backend.filter_category(category)

    def exact_published_date(self, yyyy_mm_dd: str) -> list[dict]:
        return local_backend.filter_published_date(yyyy_mm_dd)

    def phrase_search(self, phrase: str) -> list[dict]:
        return local_backend.phrase_search(phrase)

    def vector_search(self, query: str, top: int = 5) -> list[dict]:
        return local_backend.vector_search(query, top=top)

    def hybrid_search(self, query: str, caller_groups: list[str], top: int = 5) -> list[dict]:
        return local_backend.hybrid_search(query, caller_groups=caller_groups, top=top)


class AzureSearchProvider(SearchProvider):
    """Azure AI Search provider used by the full backend exercises.

    Azure SDK imports are lazy so the local-only track can run without Azure packages
    installed until the developer chooses the full Azure track.
    """

    def __init__(self, index_name: str | None = None):
        from .config import get_search_client
        self.client = get_search_client(index_name=index_name)

    def create_or_update_index(self, index_definition: dict) -> dict:
        from .rest_index import create_or_update_index
        return create_or_update_index(index_definition)

    def _normalise_results(self, results) -> list[dict]:
        return [{"key": r.key, "succeeded": r.succeeded} for r in results]

    def upload_documents(self, documents: list[dict]) -> list[dict]:
        return self._normalise_results(self.client.upload_documents(documents=documents))

    def merge_or_upload_documents(self, documents: list[dict]) -> list[dict]:
        return self._normalise_results(self.client.merge_or_upload_documents(documents=documents))

    def merge_documents(self, documents: list[dict]) -> list[dict]:
        return self._normalise_results(self.client.merge_documents(documents=documents))

    def delete_documents(self, keys: list[str]) -> list[dict]:
        return self._normalise_results(self.client.delete_documents(documents=[{"id": key} for key in keys]))

    def exact_category(self, category: str) -> list[dict]:
        results = self.client.search(search_text="*", filter=f"category eq '{category}'", select=["id", "title", "category", "publishedDate"], top=10)
        return [dict(r) for r in results]

    def exact_published_date(self, yyyy_mm_dd: str) -> list[dict]:
        # Half-open range: works cleanly for date equality at day precision.
        from datetime import date, timedelta
        y, m, d = [int(part) for part in yyyy_mm_dd.split("-")]
        next_day = (date(y, m, d) + timedelta(days=1)).isoformat()
        date_filter = f"publishedDate ge {yyyy_mm_dd}T00:00:00Z and publishedDate lt {next_day}T00:00:00Z"
        results = self.client.search(search_text="*", filter=date_filter, select=["id", "title", "publishedDate"], top=10)
        return [dict(r) for r in results]

    def phrase_search(self, phrase: str) -> list[dict]:
        results = self.client.search(search_text=f'"{phrase}"', query_type="full", search_fields=["content"], select=["id", "title"], top=10)
        return [dict(r) for r in results]

    def vector_search(self, query: str, top: int = 5) -> list[dict]:
        from azure.search.documents.models import VectorizedQuery
        query_vector = deterministic_embedding(query)
        vector_query = VectorizedQuery(vector=query_vector, k_nearest_neighbors=max(top, 5), fields="titleVector,contentVector")
        results = self.client.search(search_text=None, vector_queries=[vector_query], select=["id", "title", "category"], top=top)
        return [dict(r) for r in results]

    def hybrid_search(self, query: str, caller_groups: list[str], top: int = 5) -> list[dict]:
        from azure.search.documents.models import VectorizedQuery
        query_vector = deterministic_embedding(query)
        vector_query = VectorizedQuery(vector=query_vector, k_nearest_neighbors=20, fields="titleVector,contentVector")
        group_csv = ",".join(caller_groups)
        security_filter = f"group_ids/any(g: search.in(g, '{group_csv}'))"
        results = self.client.search(
            search_text=query,
            vector_queries=[vector_query],
            filter=security_filter,
            query_type="semantic",
            semantic_configuration_name="default-semantic-config",
            select=["id", "title", "category", "sourceFile"],
            top=top,
        )
        return [dict(r) for r in results]


def load_sample_index_definition() -> dict:
    return load_json(ROOT / "data/indexes/sample-indexes.json")[0]
