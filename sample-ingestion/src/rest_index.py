from __future__ import annotations
import json
import requests
from azure.identity import DefaultAzureCredential
from .config import SEARCH_ENDPOINT, SEARCH_API_KEY, API_VERSION, require_config


def _bearer_token() -> str:
    """Get an Azure AI Search data-plane token without shelling out to Azure CLI."""
    credential = DefaultAzureCredential()
    token = credential.get_token("https://search.azure.com/.default")
    return token.token


def create_or_update_index(index_definition: dict) -> dict:
    """Create or update an Azure AI Search index from a JSON definition."""
    require_config()
    index_name = index_definition["name"]
    url = f"{SEARCH_ENDPOINT}/indexes/{index_name}?api-version={API_VERSION}"
    headers = {"Content-Type": "application/json"}
    if SEARCH_API_KEY:
        headers["api-key"] = SEARCH_API_KEY
    else:
        headers["Authorization"] = f"Bearer {_bearer_token()}"
    response = requests.put(url, headers=headers, data=json.dumps(index_definition), timeout=60)
    response.raise_for_status()
    return response.json()
