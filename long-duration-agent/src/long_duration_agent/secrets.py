"""Fetches secrets from Azure Key Vault when configured, falling back to a plain env var
for local dev - starting with the Content Safety API key (content_safety.py's "azure" mode).
Extend ``get_secret`` to any other value that shouldn't live in an env var in production (e.g.
``AZURE_OPENAI_API_KEY``).

Uses the synchronous Key Vault/Identity clients, not the aio variants - deliberately, and
for the same reason identity.py's JWKS fetch is synchronous: this only ever runs once per
``LDA_KEY_VAULT_CACHE_SECONDS`` window (default 1 hour), not on every request, so a brief
blocking call here and there is a reasonable trade-off against threading an async Key Vault
client through every caller for a fetch that's this infrequent.
"""

from __future__ import annotations

import time
from typing import Any

from .config import get_settings

_CACHE: dict[str, dict[str, Any]] = {}  # secret_name -> {"value": str, "fetched_at": float}


def get_secret(secret_name: str, *, env_fallback: str) -> str:
    """Returns the secret's current value.

    If ``LDA_KEY_VAULT_URL`` isn't configured, returns ``env_fallback`` unchanged - this is
    what keeps local/demo runs working with a plain .env value. Otherwise fetches (and
    caches) the named secret from that vault.
    """
    settings = get_settings()
    if not settings.lda_key_vault_url:
        return env_fallback

    now = time.monotonic()
    cached = _CACHE.get(secret_name)
    if cached is not None and now - cached["fetched_at"] <= settings.lda_key_vault_cache_seconds:
        return cached["value"]

    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=settings.lda_key_vault_url, credential=DefaultAzureCredential())
    secret = client.get_secret(secret_name)
    _CACHE[secret_name] = {"value": secret.value, "fetched_at": now}
    return secret.value


def get_content_safety_api_key() -> str:
    settings = get_settings()
    return get_secret(
        settings.lda_key_vault_content_safety_key_secret_name, env_fallback=settings.azure_content_safety_api_key
    )


def reset_secret_cache() -> None:
    """Test helper: forces the next get_secret() call to re-fetch instead of using the cache."""
    _CACHE.clear()
