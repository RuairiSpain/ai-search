import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request

import long_duration_agent.identity as identity
from long_duration_agent.config import get_settings

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _b64url(n: int) -> str:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@pytest.fixture
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "n": _b64url(numbers.n),
        "e": _b64url(numbers.e),
        "kid": "test-kid",
        "use": "sig",
        "alg": "RS256",
    }
    return key, jwk


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch, rsa_keypair):
    _key, jwk = rsa_keypair

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"keys": [jwk]}

    monkeypatch.setattr(identity.httpx, "get", lambda url, timeout=None: FakeResp())
    identity._JWKS_CACHE.clear()
    yield
    identity._JWKS_CACHE.clear()


def _make_token(rsa_keypair, **overrides) -> str:
    key, _jwk = rsa_keypair
    now = int(time.time())
    claims = {
        "tid": TENANT_ID,
        "oid": "user-object-id-123",
        "aud": "api://my-app-id",
        "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "scp": "Invocations.Invoke",
        "name": "Ada Lovelace",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-kid"})


def _request_with_token(token: str) -> Request:
    scope = {"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]}
    return Request(scope)


def _configure(monkeypatch, **env):
    monkeypatch.setenv("LDA_IDENTITY_MODE", "entra")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_valid_token_resolves_caller_identity(monkeypatch, rsa_keypair):
    _configure(monkeypatch, ENTRA_AUDIENCE="api://my-app-id", ENTRA_REQUIRED_SCOPE="Invocations.Invoke")
    caller = identity._resolve_entra(_request_with_token(_make_token(rsa_keypair)))
    assert caller.tenant_id == TENANT_ID
    assert caller.user_object_id == "user-object-id-123"
    assert caller.display_name == "Ada Lovelace"


def test_missing_audience_configuration_fails_closed(monkeypatch, rsa_keypair):
    _configure(monkeypatch, ENTRA_AUDIENCE="")
    with pytest.raises(HTTPException) as exc_info:
        identity._resolve_entra(_request_with_token(_make_token(rsa_keypair)))
    assert exc_info.value.status_code == 500


def test_wrong_audience_is_rejected(monkeypatch, rsa_keypair):
    _configure(monkeypatch, ENTRA_AUDIENCE="api://my-app-id")
    token = _make_token(rsa_keypair, aud="api://someone-elses-app")
    with pytest.raises(HTTPException) as exc_info:
        identity._resolve_entra(_request_with_token(token))
    assert exc_info.value.status_code == 401


def test_issuer_not_matching_claimed_tenant_is_rejected(monkeypatch, rsa_keypair):
    _configure(monkeypatch, ENTRA_AUDIENCE="api://my-app-id")
    token = _make_token(rsa_keypair, iss="https://evil.example.com/fake/v2.0")
    with pytest.raises(HTTPException) as exc_info:
        identity._resolve_entra(_request_with_token(token))
    assert exc_info.value.status_code == 401
    assert "issuer" in exc_info.value.detail.lower()


def test_v1_issuer_format_is_accepted(monkeypatch, rsa_keypair):
    _configure(monkeypatch, ENTRA_AUDIENCE="api://my-app-id")
    token = _make_token(rsa_keypair, iss=f"https://sts.windows.net/{TENANT_ID}/")
    caller = identity._resolve_entra(_request_with_token(token))
    assert caller.tenant_id == TENANT_ID


def test_missing_required_scope_is_rejected(monkeypatch, rsa_keypair):
    _configure(monkeypatch, ENTRA_AUDIENCE="api://my-app-id", ENTRA_REQUIRED_SCOPE="Admin.Access")
    with pytest.raises(HTTPException) as exc_info:
        identity._resolve_entra(_request_with_token(_make_token(rsa_keypair)))
    assert exc_info.value.status_code == 403


def test_missing_required_role_is_rejected(monkeypatch, rsa_keypair):
    _configure(monkeypatch, ENTRA_AUDIENCE="api://my-app-id", ENTRA_REQUIRED_ROLE="Admin")
    with pytest.raises(HTTPException) as exc_info:
        identity._resolve_entra(_request_with_token(_make_token(rsa_keypair)))
    assert exc_info.value.status_code == 403


def test_required_role_present_is_accepted(monkeypatch, rsa_keypair):
    _configure(monkeypatch, ENTRA_AUDIENCE="api://my-app-id", ENTRA_REQUIRED_ROLE="Admin")
    token = _make_token(rsa_keypair, roles=["Admin", "Other"])
    caller = identity._resolve_entra(_request_with_token(token))
    assert caller.tenant_id == TENANT_ID


def test_unexpected_tenant_is_rejected(monkeypatch, rsa_keypair):
    _configure(monkeypatch, ENTRA_AUDIENCE="api://my-app-id", ENTRA_TENANT_ID="22222222-2222-2222-2222-222222222222")
    with pytest.raises(HTTPException) as exc_info:
        identity._resolve_entra(_request_with_token(_make_token(rsa_keypair)))
    assert exc_info.value.status_code == 401


def test_jwks_cache_is_keyed_per_tenant(monkeypatch, rsa_keypair):
    """A second tenant's request must not be checked against the first tenant's cached keys."""
    _configure(monkeypatch, ENTRA_AUDIENCE="api://my-app-id")
    identity._resolve_entra(_request_with_token(_make_token(rsa_keypair)))
    assert TENANT_ID in identity._JWKS_CACHE

    other_tenant = "33333333-3333-3333-3333-333333333333"
    assert other_tenant not in identity._JWKS_CACHE
