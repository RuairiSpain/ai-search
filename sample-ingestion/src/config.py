from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SEARCH_ENDPOINT = os.getenv("SEARCH_ENDPOINT", "").rstrip("/")
SEARCH_INDEX_NAME = os.getenv("SEARCH_INDEX_NAME", "rag-documents-index")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY", "")
API_VERSION = os.getenv("SEARCH_API_VERSION", "2026-04-01")


def require_config() -> None:
    if not SEARCH_ENDPOINT:
        raise RuntimeError("SEARCH_ENDPOINT is not set. Copy .env.example to .env and add your Search endpoint.")


def get_credential():
    """Use an API key when provided; otherwise use Microsoft Entra ID."""
    if SEARCH_API_KEY:
        return AzureKeyCredential(SEARCH_API_KEY)
    return DefaultAzureCredential()


def get_search_client(index_name: str | None = None) -> SearchClient:
    require_config()
    return SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=index_name or SEARCH_INDEX_NAME,
        credential=get_credential(),
        api_version=API_VERSION,
    )
