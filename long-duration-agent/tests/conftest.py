import shutil
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Points every stateful module at a fresh tmp directory and fast pipeline waits."""
    monkeypatch.setenv("LDA_USE_STUB_TRANSLATOR", "1")
    monkeypatch.setenv("LDA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("LDA_LOCAL_STORAGE_ROOT", str(tmp_path / "blob-store"))
    monkeypatch.setenv("LDA_STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("LDA_IDENTITY_MODE", "dev")
    monkeypatch.setenv("LDA_BROKER_SIGNING_KEY", "test-signing-key")
    monkeypatch.setenv("LDA_BROKER_BASE_URL", "http://localhost:8081")
    monkeypatch.setenv("LDA_WAIT_AFTER_SAVE_SECONDS", "0")
    monkeypatch.setenv("LDA_WAIT_BEFORE_UPLOAD_SECONDS", "0")
    monkeypatch.chdir(tmp_path)

    from long_duration_agent.config import get_settings
    from long_duration_agent.durable.engine import reset_checkpoint_storage_cache
    from long_duration_agent.storage.blob_store import reset_blob_store_cache
    from long_duration_agent.storage.metadata_store import reset_metadata_store_cache

    get_settings.cache_clear()
    reset_blob_store_cache()
    reset_metadata_store_cache()
    reset_checkpoint_storage_cache()

    yield

    get_settings.cache_clear()
    reset_blob_store_cache()
    reset_metadata_store_cache()
    reset_checkpoint_storage_cache()
