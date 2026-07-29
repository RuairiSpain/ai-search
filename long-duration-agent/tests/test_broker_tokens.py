import time

import pytest

from long_duration_agent.broker.tokens import (
    InvalidDownloadTokenError,
    build_download_link,
    issue_download_token,
    verify_download_token,
)


def test_issue_and_verify_round_trip():
    token, expires_at_epoch = issue_download_token(
        artifact_id="artifact-1", tenant_id="tenant-a", user_object_id="user-1"
    )
    payload = verify_download_token(token)
    assert payload.artifact_id == "artifact-1"
    assert payload.tenant_id == "tenant-a"
    assert payload.user_object_id == "user-1"
    assert payload.expires_at_epoch == expires_at_epoch


def test_tampered_signature_is_rejected():
    token, _ = issue_download_token(artifact_id="a", tenant_id="t", user_object_id="u")
    payload_b64, _signature = token.split(".", 1)
    tampered = f"{payload_b64}.not-a-real-signature"
    with pytest.raises(InvalidDownloadTokenError):
        verify_download_token(tampered)


def test_expired_token_is_rejected(monkeypatch):
    monkeypatch.setenv("LDA_DOWNLOAD_TOKEN_TTL_MINUTES", "15")
    from long_duration_agent.config import get_settings

    get_settings.cache_clear()
    token, _ = issue_download_token(artifact_id="a", tenant_id="t", user_object_id="u")

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 16 * 60)
    with pytest.raises(InvalidDownloadTokenError):
        verify_download_token(token)


def test_build_download_link_embeds_artifact_id_and_token():
    url, expires_at = build_download_link(artifact_id="abc-123", tenant_id="t", user_object_id="u")
    assert "/artifacts/abc-123/download" in url
    assert "token=" in url
    assert expires_at.timestamp() > time.time()


def test_two_links_for_the_same_artifact_use_different_tokens():
    url1, _ = build_download_link(artifact_id="abc-123", tenant_id="t", user_object_id="u")
    url2, _ = build_download_link(artifact_id="abc-123", tenant_id="t", user_object_id="u")
    assert url1 != url2  # each call always mints a fresh token, never reuses one
