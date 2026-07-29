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

    # Hosted-agent local scratch workspace ($HOME/artifacts equivalent) - temporary only,
    # deleted once the durable copy is uploaded. See workspace.py.
    lda_workspace_root: str = ".data/workspace"

    # Storage backend
    lda_storage_backend: str = "local"  # "local" | "azurite" | "azure"
    lda_local_storage_root: str = ".data/blob-store"
    azure_storage_account_url: str = ""
    azure_storage_container: str = "artifacts"

    # Azurite (local Azure Storage emulator) connection string, used when any *_backend
    # setting below is "azurite". This is Azurite's published, well-known development
    # account key - it authenticates against a local emulator only and is not a secret
    # (see https://learn.microsoft.com/azure/storage/common/storage-use-azurite).
    azurite_connection_string: str = (
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
        "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
        "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
        "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
    )

    # Orchestration checkpoint storage: "file" is a single host's local disk (fine for a
    # single instance/demo); "azurite"/"azure" is Azure Table Storage, required once more
    # than one hosted-agent replica needs to see the same in-flight operations.
    lda_checkpoint_backend: str = "file"  # "file" | "azurite" | "azure"
    lda_checkpoint_table_name: str = "workflowcheckpoints"

    # Operation/artifact bookkeeping: "sqlite" is a single host's local file (fine for a
    # single instance/demo); "azurite"/"azure" are the multi-instance-safe Table Storage
    # backend, against the local emulator or a real account respectively.
    lda_metadata_backend: str = "sqlite"  # "sqlite" | "azurite" | "azure"
    lda_operations_table_name: str = "operations"
    lda_artifacts_table_name: str = "artifacts"
    lda_steering_table_name: str = "steeringmessages"
    azure_table_account_url: str = ""  # e.g. https://<account>.table.core.windows.net

    # Orchestration + metadata state (sqlite backend only)
    lda_state_db_path: str = ".data/state.db"

    # Stale-operation sweep (see stale_operations.py): an operation stuck in_progress or
    # waiting_hitl (crashed worker, abandoned HITL prompt) past this many hours is force-
    # stopped and cleaned up, the same way a user-initiated "stop" decision would.
    lda_operation_stale_hours: int = 6

    # Key Vault (see secrets.py): when set, AZURE_CONTENT_SAFETY_API_KEY is fetched from this
    # vault instead of the env var below, cached for lda_key_vault_cache_seconds.
    lda_key_vault_url: str = ""
    lda_key_vault_content_safety_key_secret_name: str = "lda-content-safety-api-key"
    lda_key_vault_cache_seconds: int = 3600

    # Observability (see observability.py)
    lda_otel_exporter: str = "none"  # "none" | "console" | "otlp"
    lda_otel_endpoint: str = ""  # OTLP collector endpoint, used when lda_otel_exporter == "otlp"
    lda_service_name: str = "long-duration-agent"
    lda_metrics_enabled: bool = True

    # Pipeline pacing (seconds). Defaults match the spec's "wait 5s" / "wait 2s" steps;
    # override in tests to keep the suite fast.
    lda_wait_after_save_seconds: float = 5.0
    lda_wait_before_upload_seconds: float = 2.0

    # Artifact policy
    lda_artifact_ttl_hours: int = 24
    lda_max_input_chars: int = 1_000_000
    lda_max_markdown_bytes: int = 5 * 1024 * 1024

    # Download links: a real, time-limited Azure Blob SAS URL minted directly against Blob
    # Storage (storage/blob_store.py's generate_download_url) - no broker/proxy in between, so
    # this is the only authorization a download gets beyond knowing the link itself. Keep this
    # short; anyone holding the URL can use it until it expires.
    lda_download_sas_ttl_minutes: int = 15

    # Rate limiting (see rate_limit.py): in-memory, per-caller (tenant_id + user_object_id)
    # sliding window over 60 seconds, applied only to genuinely new operations (not resumes/
    # reconnects) - the one call that costs a model invocation per request. 0 disables it.
    # Downloads aren't rate limited here - they never pass through this app (see
    # generate_download_url above); use Blob Storage's own throttling/logging for that.
    lda_rate_limit_enabled: bool = True
    lda_rate_limit_invocations_per_minute: int = 30

    # Content safety guardrail (see content_safety.py), checked on the English prompt before
    # Translate. "off" (default, unchanged demo behavior) | "blocklist" (offline, deterministic
    # substring match against lda_content_safety_blocklist) | "azure" (real Azure AI Content
    # Safety analyze_text call).
    lda_content_safety_mode: str = "off"  # "off" | "blocklist" | "azure"
    lda_content_safety_blocklist: str = ""  # comma-separated terms, "blocklist" mode only
    azure_content_safety_endpoint: str = ""
    azure_content_safety_api_key: str = ""
    # FourSeverityLevels output is 0/2/4/6 per category; block at or above this. 4 is Azure's
    # own default "Medium" threshold.
    lda_content_safety_max_severity: int = 4

    # Identity
    lda_identity_mode: str = "dev"  # "dev" | "entra"
    entra_tenant_id: str = ""
    entra_audience: str = ""
    # If set, the token's "iss" claim must match https://login.microsoftonline.com/{tenant}/v2.0
    # for whichever tenant it claims - see identity.py. Recommended: leave True in production.
    entra_require_issuer_match: bool = True
    # If set, the token must carry this value in its "scp" (delegated permission) claim.
    entra_required_scope: str = ""
    # If set, the token must carry this value in its "roles" (app permission) claim.
    entra_required_role: str = ""

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

    @property
    def workspace_root(self) -> Path:
        path = Path(self.lda_workspace_root)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
