import importlib.util
from unittest import mock

import pytest

from long_duration_agent import secrets
from long_duration_agent.config import get_settings

try:
    KEY_VAULT_INSTALLED = importlib.util.find_spec("azure.keyvault.secrets") is not None
except ModuleNotFoundError:
    # azure is a namespace package assembled from several separately-installed subpackages;
    # find_spec raises (rather than returning None) when an intermediate segment - here
    # "azure.keyvault" - doesn't exist anywhere, i.e. azure-keyvault-secrets isn't installed.
    KEY_VAULT_INSTALLED = False
requires_key_vault_sdk = pytest.mark.skipif(
    not KEY_VAULT_INSTALLED, reason="azure-keyvault-secrets not installed (pip install '.[production]')"
)


@pytest.fixture(autouse=True)
def reset_secret_state():
    secrets.reset_secret_cache()
    yield
    secrets.reset_secret_cache()


def test_falls_back_to_env_var_when_key_vault_not_configured(monkeypatch):
    monkeypatch.setenv("LDA_KEY_VAULT_URL", "")
    monkeypatch.setenv("AZURE_CONTENT_SAFETY_API_KEY", "env-value")
    get_settings.cache_clear()

    assert secrets.get_content_safety_api_key() == "env-value"


@requires_key_vault_sdk
def test_fetches_from_key_vault_when_configured(monkeypatch):
    monkeypatch.setenv("LDA_KEY_VAULT_URL", "https://fake-vault.vault.azure.net")
    monkeypatch.setenv("AZURE_CONTENT_SAFETY_API_KEY", "env-value-unused")
    get_settings.cache_clear()

    fake_secret = mock.Mock(value="vault-value")
    with (
        mock.patch("azure.keyvault.secrets.SecretClient") as mock_client_cls,
        mock.patch("azure.identity.DefaultAzureCredential"),
    ):
        mock_client_cls.return_value.get_secret.return_value = fake_secret
        value = secrets.get_content_safety_api_key()

    assert value == "vault-value"


@requires_key_vault_sdk
def test_key_vault_fetch_is_cached_within_the_ttl(monkeypatch):
    monkeypatch.setenv("LDA_KEY_VAULT_URL", "https://fake-vault.vault.azure.net")
    monkeypatch.setenv("LDA_KEY_VAULT_CACHE_SECONDS", "3600")
    get_settings.cache_clear()

    fake_secret = mock.Mock(value="vault-value")
    with (
        mock.patch("azure.keyvault.secrets.SecretClient") as mock_client_cls,
        mock.patch("azure.identity.DefaultAzureCredential"),
    ):
        mock_client_cls.return_value.get_secret.return_value = fake_secret
        secrets.get_content_safety_api_key()
        secrets.get_content_safety_api_key()
        assert mock_client_cls.return_value.get_secret.call_count == 1


@requires_key_vault_sdk
def test_key_vault_fetch_refreshes_after_ttl_expires(monkeypatch):
    monkeypatch.setenv("LDA_KEY_VAULT_URL", "https://fake-vault.vault.azure.net")
    monkeypatch.setenv("LDA_KEY_VAULT_CACHE_SECONDS", "0")
    get_settings.cache_clear()

    fake_secret = mock.Mock(value="vault-value")
    with (
        mock.patch("azure.keyvault.secrets.SecretClient") as mock_client_cls,
        mock.patch("azure.identity.DefaultAzureCredential"),
    ):
        mock_client_cls.return_value.get_secret.return_value = fake_secret
        secrets.get_content_safety_api_key()
        secrets.get_content_safety_api_key()
        assert mock_client_cls.return_value.get_secret.call_count == 2
