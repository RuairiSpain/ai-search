"""Runtime configuration, loaded from environment variables (.env supported).

Every knob that differs between the local demo and a real private-network
Azure deployment lives here, so the rest of the codebase never reads
``os.environ`` directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Translation model
    foundry_project_endpoint: str = ""
    foundry_model: str = "gpt-4o-mini"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_model: str = ""
    lda_use_stub_translator: bool = True

    # Storage backend
    lda_storage_backend: str = "local"  # "local" | "azurite" | "azure"
    lda_local_storage_root: str = ".data/blob-store"
    azure_storage_account_url: str = ""
    azure_storage_container: str = "artifacts"

    # Azurite (local Azure Storage emulator) connection string, used only when
    # lda_storage_backend == "azurite". This is Azurite's published, well-known
    # development account key - it authenticates against a local emulator only
    # and is not a secret (see https://learn.microsoft.com/azure/storage/common/storage-use-azurite).
    azurite_connection_string: str = (
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
        "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    )

    # Orchestration + metadata state
    lda_state_db_path: str = ".data/state.db"

    # Pipeline pacing (seconds). Defaults match the spec's "wait 5s" / "wait 2s" steps;
    # override in tests to keep the suite fast.
    lda_wait_after_save_seconds: float = 5.0
    lda_wait_before_upload_seconds: float = 2.0

    # Artifact policy
    lda_artifact_ttl_hours: int = 24
    lda_download_token_ttl_minutes: int = 15
    lda_max_input_chars: int = 1_000_000
    lda_max_markdown_bytes: int = 5 * 1024 * 1024

    # Broker
    lda_broker_signing_key: str = "dev-only-change-me"
    lda_broker_base_url: str = "http://localhost:8081"

    # Identity
    lda_identity_mode: str = "dev"  # "dev" | "entra"
    entra_tenant_id: str = ""
    entra_audience: str = ""

    @property
    def state_db_path(self) -> Path:
        path = Path(self.lda_state_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def local_storage_root(self) -> Path:
        path = Path(self.lda_local_storage_root)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
