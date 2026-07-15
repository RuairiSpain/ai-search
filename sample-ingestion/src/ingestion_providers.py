"""Provider abstractions for Azure AI Search ingestion demos.

This file intentionally keeps Azure SDK imports lazy so the local exercises can be
imported and tested without requiring Azure packages until the Azure-backed paths
are used.

Providers included:
- LocalSearchProvider: no Azure dependency, fake local index for workshops.
- AzureSearchProvider: direct Azure AI Search push/query provider.
- FabricLakehouseProvider: OneLake/Lakehouse file ingestion through an ADLS Gen2
  compatible indexer pattern.
- FabricWarehouseProvider: structured Fabric Warehouse/Lakehouse table ingestion
  through the Azure AI Search push API.

Important design distinction:
- Fabric Lakehouse/OneLake files are best handled through indexers + skillsets
  when you want document cracking, OCR, chunking, integrated vectorisation and
  index projections.
- Fabric Warehouse tables are usually best handled with a push pipeline that
  transforms rows into search documents and calls upload/merge/delete APIs.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


JsonDict = dict[str, Any]
DocumentMapper = Callable[[Mapping[str, Any]], JsonDict]


@dataclass
class IndexingResult:
    key: str
    succeeded: bool
    error_message: str | None = None


class SearchIngestionProvider(ABC):
    """Common contract for local, Azure Search and Fabric ingestion providers."""

    @abstractmethod
    def upload_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        """Upload or replace documents."""

    @abstractmethod
    def merge_or_upload_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        """Merge if document exists, upload if it does not."""

    @abstractmethod
    def merge_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        """Partially update existing documents."""

    @abstractmethod
    def delete_documents(self, keys: Sequence[str]) -> list[IndexingResult]:
        """Delete documents by key."""


class LocalSearchProvider(SearchIngestionProvider):
    """Local fake provider for no-Azure exercises.

    Uses sample-ingestion/src/local_backend.py. This is useful for workshops where
    developers should learn the concepts before deploying infrastructure.
    """

    def upload_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        from . import local_backend

        results = local_backend.upload_documents(list(documents))
        return [IndexingResult(key=r["key"], succeeded=r["succeeded"], error_message=r.get("error")) for r in results]

    def merge_or_upload_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        from . import local_backend

        results = local_backend.merge_or_upload_documents(list(documents))
        return [IndexingResult(key=r["key"], succeeded=r["succeeded"], error_message=r.get("error")) for r in results]

    def merge_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        from . import local_backend

        results = local_backend.merge_documents(list(documents))
        return [IndexingResult(key=r["key"], succeeded=r["succeeded"], error_message=r.get("error")) for r in results]

    def delete_documents(self, keys: Sequence[str]) -> list[IndexingResult]:
        from . import local_backend

        results = local_backend.delete_documents(keys)
        return [IndexingResult(key=r["key"], succeeded=r["succeeded"], error_message=r.get("error")) for r in results]


class AzureSearchProvider(SearchIngestionProvider):
    """Direct Azure AI Search provider using the push API.

    Use this provider when your pipeline already has JSON documents that match the
    search index schema. It works for structured data and for custom parsers that
    handle their own cracking, chunking and embedding generation.
    """

    def __init__(
        self,
        endpoint: str,
        index_name: str,
        api_key: str | None = None,
        credential: Any | None = None,
        api_version: str | None = None,
    ) -> None:
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
        from azure.search.documents import SearchClient

        if credential is None:
            credential = AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()

        kwargs = {"endpoint": endpoint.rstrip("/"), "index_name": index_name, "credential": credential}
        if api_version:
            kwargs["api_version"] = api_version
        self.client = SearchClient(**kwargs)
        self.index_name = index_name

    @staticmethod
    def _normalise(results: Iterable[Any]) -> list[IndexingResult]:
        output: list[IndexingResult] = []
        for result in results:
            output.append(
                IndexingResult(
                    key=getattr(result, "key", ""),
                    succeeded=bool(getattr(result, "succeeded", False)),
                    error_message=getattr(result, "error_message", None),
                )
            )
        return output

    def upload_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        return self._normalise(self.client.upload_documents(documents=list(documents)))

    def merge_or_upload_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        return self._normalise(self.client.merge_or_upload_documents(documents=list(documents)))

    def merge_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        return self._normalise(self.client.merge_documents(documents=list(documents)))

    def delete_documents(self, keys: Sequence[str]) -> list[IndexingResult]:
        return self._normalise(self.client.delete_documents(documents=[{"id": key} for key in keys]))


@dataclass
class FabricLakehouseIndexerConfig:
    """Configuration for OneLake/Lakehouse file ingestion via an indexer.

    The connection string should point to an ADLS Gen2-compatible endpoint or
    storage account path that your Search service can access. For OneLake, this is
    usually provided by your platform/Fabric configuration and identity model.
    """

    data_source_name: str
    indexer_name: str
    skillset_name: str
    target_index_name: str
    connection_string: str
    container_name: str
    query: str | None = None
    description: str = "Fabric Lakehouse / OneLake file ingestion data source"


class FabricLakehouseProvider:
    """File-oriented Fabric Lakehouse/OneLake provider using Search indexers.

    Use this for unstructured files stored in OneLake/Lakehouse locations exposed
    through ADLS Gen2-compatible access. It creates a data source and an indexer.
    If you pass a skillset name, the indexer can run document cracking, OCR,
    chunking, embeddings and index projections.

    This provider does not upload files itself. It assumes the files already exist
    in the Lakehouse/OneLake location referenced by the data source connection.
    """

    def __init__(self, search_endpoint: str, api_key: str | None = None, credential: Any | None = None) -> None:
        from azure.core.credentials import AzureKeyCredential
        from azure.identity import DefaultAzureCredential
        from azure.search.documents.indexes import SearchIndexerClient

        if credential is None:
            credential = AzureKeyCredential(api_key) if api_key else DefaultAzureCredential()
        self.indexer_client = SearchIndexerClient(endpoint=search_endpoint.rstrip("/"), credential=credential)

    def create_or_update_data_source(self, config: FabricLakehouseIndexerConfig) -> Any:
        from azure.search.documents.indexes.models import (
            SearchIndexerDataContainer,
            SearchIndexerDataSourceConnection,
        )

        container = SearchIndexerDataContainer(name=config.container_name, query=config.query)
        data_source = SearchIndexerDataSourceConnection(
            name=config.data_source_name,
            type="adlsgen2",
            connection_string=config.connection_string,
            container=container,
            description=config.description,
        )
        return self.indexer_client.create_or_update_data_source_connection(data_source)

    def create_or_update_indexer(self, config: FabricLakehouseIndexerConfig, field_mappings: list[Any] | None = None, output_field_mappings: list[Any] | None = None, parameters: Any | None = None) -> Any:
        from azure.search.documents.indexes.models import SearchIndexer

        indexer = SearchIndexer(
            name=config.indexer_name,
            data_source_name=config.data_source_name,
            target_index_name=config.target_index_name,
            skillset_name=config.skillset_name,
            field_mappings=field_mappings,
            output_field_mappings=output_field_mappings,
            parameters=parameters,
        )
        return self.indexer_client.create_or_update_indexer(indexer)

    def run_indexer(self, indexer_name: str) -> None:
        self.indexer_client.run_indexer(indexer_name)

    def get_indexer_status(self, indexer_name: str) -> Any:
        return self.indexer_client.get_indexer_status(indexer_name)

    def provision_and_run(self, config: FabricLakehouseIndexerConfig, parameters: Any | None = None) -> Any:
        """Create/update datasource and indexer, run it, then return the status."""

        self.create_or_update_data_source(config)
        self.create_or_update_indexer(config, parameters=parameters)
        self.run_indexer(config.indexer_name)
        return self.get_indexer_status(config.indexer_name)


@dataclass
class FabricWarehouseProvider(SearchIngestionProvider):
    """Structured Fabric Warehouse/Lakehouse table provider using Search push APIs.

    There is no file cracking here. This provider expects rows from Fabric SQL,
    Spark, a Fabric notebook, a DataFrame, or any iterable of dictionaries. You map
    each source row into the target Azure AI Search document schema and push it to
    the index.
    """

    search_provider: AzureSearchProvider
    document_mapper: DocumentMapper | None = None
    key_field: str = "id"
    default_values: JsonDict = field(default_factory=dict)

    def _map_row(self, row: Mapping[str, Any]) -> JsonDict:
        if self.document_mapper:
            document = self.document_mapper(row)
        else:
            document = dict(row)
        for key, value in self.default_values.items():
            document.setdefault(key, value)
        if self.key_field not in document:
            raise ValueError(f"Mapped document is missing required key field '{self.key_field}': {document}")
        return document

    def map_rows(self, rows: Iterable[Mapping[str, Any]]) -> list[JsonDict]:
        return [self._map_row(row) for row in rows]

    def map_dataframe(self, dataframe: Any) -> list[JsonDict]:
        """Map a pandas or Spark-style DataFrame to search documents.

        Pandas DataFrame: uses to_dict('records').
        Spark DataFrame: uses row.asDict(recursive=True) after collect(). For large
        datasets, do partitioned/batched writes instead of collect().
        """

        if hasattr(dataframe, "to_dict"):
            return self.map_rows(dataframe.to_dict(orient="records"))
        if hasattr(dataframe, "collect"):
            return self.map_rows(row.asDict(recursive=True) for row in dataframe.collect())
        raise TypeError("Expected a pandas DataFrame, Spark DataFrame, or iterable of mappings.")

    def upload_rows(self, rows: Iterable[Mapping[str, Any]], batch_size: int = 500) -> list[IndexingResult]:
        return self._push_in_batches(self.map_rows(rows), self.search_provider.upload_documents, batch_size)

    def merge_or_upload_rows(self, rows: Iterable[Mapping[str, Any]], batch_size: int = 500) -> list[IndexingResult]:
        return self._push_in_batches(self.map_rows(rows), self.search_provider.merge_or_upload_documents, batch_size)

    def merge_rows(self, rows: Iterable[Mapping[str, Any]], batch_size: int = 500) -> list[IndexingResult]:
        return self._push_in_batches(self.map_rows(rows), self.search_provider.merge_documents, batch_size)

    @staticmethod
    def _push_in_batches(documents: Sequence[JsonDict], operation: Callable[[Sequence[JsonDict]], list[IndexingResult]], batch_size: int) -> list[IndexingResult]:
        results: list[IndexingResult] = []
        for start in range(0, len(documents), batch_size):
            results.extend(operation(documents[start:start + batch_size]))
        return results

    # SearchIngestionProvider methods for already mapped documents.
    def upload_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        return self.search_provider.upload_documents(documents)

    def merge_or_upload_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        return self.search_provider.merge_or_upload_documents(documents)

    def merge_documents(self, documents: Sequence[JsonDict]) -> list[IndexingResult]:
        return self.search_provider.merge_documents(documents)

    def delete_documents(self, keys: Sequence[str]) -> list[IndexingResult]:
        return self.search_provider.delete_documents(keys)


# Example row mapper for Fabric Warehouse rows.
def default_fabric_warehouse_row_mapper(row: Mapping[str, Any]) -> JsonDict:
    """Map a typical Fabric row into the sample Azure AI Search schema.

    Adjust field names to your warehouse table. This mapper is intentionally
    conservative and preserves common governance fields for filtering/trimming.
    """

    raw_id = row.get("id") or row.get("document_id") or row.get("record_id")
    if raw_id is None:
        raise ValueError("Row must contain id, document_id, or record_id")

    title = row.get("title") or row.get("name") or f"Record {raw_id}"
    content = row.get("content") or row.get("description") or row.get("summary") or ""

    return {
        "id": str(raw_id),
        "title": str(title),
        "content": str(content),
        "category": str(row.get("category", "FabricWarehouse")),
        "sourceFile": str(row.get("source", "fabric-warehouse")),
        "businessUnit": row.get("businessUnit") or row.get("business_unit"),
        "classification": row.get("classification", "Internal"),
        "sensitivityLabel": row.get("sensitivityLabel") or row.get("sensitivity_label"),
        "group_ids": row.get("group_ids") or row.get("allowed_groups") or [],
        "publishedDate": row.get("publishedDate") or row.get("created_at"),
        "lastUpdated": row.get("lastUpdated") or row.get("updated_at"),
    }
