"""Unit tests for principal extraction (docs/05-tier2-hosted-agents.md
§3.3 verification requirements). No network: the JWKS lookup is
monkeypatched with a locally generated RSA key so these run offline.
"""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from gateway.auth.principal import AuthError, EntraValidator, Principal

TENANT_ID = "11111111-1111-1111-1111-111111111111"
AUDIENCE = "api://a2a-gateway"


@pytest.fixture()
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _sign(rsa_key, claims: dict) -> str:
    return jwt.encode(claims, rsa_key, algorithm="RS256", headers={"kid": "test-key"})


def _validator(rsa_key) -> EntraValidator:
    validator = EntraValidator(tenant_id=TENANT_ID, audience=AUDIENCE)

    class _FakeSigningKey:
        key = rsa_key.public_key()

    validator._jwks.get_signing_key_from_jwt = lambda token: _FakeSigningKey()  # type: ignore[method-assign]
    return validator


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "oid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "tid": TENANT_ID,
        "aud": AUDIENCE,
        "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
        "exp": now + 300,
        "iat": now,
    }
    claims.update(overrides)
    return claims


def test_valid_token_yields_qualified_principal(rsa_key):
    validator = _validator(rsa_key)
    token = _sign(rsa_key, _base_claims())

    principal = validator.principal_from(f"Bearer {token}")

    assert principal.subject == f"{TENANT_ID}.aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert principal.tenant == TENANT_ID
    assert principal.user_identity_header() == principal.subject


def test_missing_bearer_prefix_rejected(rsa_key):
    validator = _validator(rsa_key)
    with pytest.raises(AuthError):
        validator.principal_from("not-a-bearer-token")


def test_missing_authorization_rejected(rsa_key):
    validator = _validator(rsa_key)
    with pytest.raises(AuthError):
        validator.principal_from(None)


def test_wrong_audience_rejected(rsa_key):
    validator = _validator(rsa_key)
    token = _sign(rsa_key, _base_claims(aud="api://someone-else"))
    with pytest.raises(AuthError):
        validator.principal_from(f"Bearer {token}")


def test_expired_token_rejected(rsa_key):
    validator = _validator(rsa_key)
    token = _sign(rsa_key, _base_claims(exp=int(time.time()) - 10))
    with pytest.raises(AuthError):
        validator.principal_from(f"Bearer {token}")


def test_missing_oid_rejected(rsa_key):
    validator = _validator(rsa_key)
    claims = _base_claims()
    del claims["oid"]
    token = _sign(rsa_key, claims)
    with pytest.raises(AuthError):
        validator.principal_from(f"Bearer {token}")


@pytest.mark.parametrize(
    "subject",
    [
        "",
        "a" * 257,
        "has a space",
        "has/a/slash",
        "<script>",
    ],
)
def test_user_identity_header_rejects_bad_charset(subject):
    principal = Principal(subject=subject, tenant="t")
    with pytest.raises(AuthError):
        principal.user_identity_header()


def test_user_identity_header_accepts_allowed_charset():
    principal = Principal(subject="tenant.oid-123_abc:def@example", tenant="t")
    assert principal.user_identity_header() == principal.subject
