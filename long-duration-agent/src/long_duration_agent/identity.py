"""Resolves the authenticated caller for every request.

Ownership of an artifact is always derived from the *validated* token, never
from a tenant/user id supplied in a request body. This module is what makes
that guarantee possible for both the hosted-agent invocation endpoint and the
Artifact Broker download endpoint, whether the caller reaches us directly, or
via a Bot Framework / Copilot Studio channel that already performed an
on-behalf-of (OBO) exchange upstream. In every case what lands here is a
bearer token for the signed-in user; we validate it and pull ``tid``/``oid``
from its claims. See docs/chat-integrations.md for how each channel
(Teams, Copilot Studio, M365 Copilot) gets a user token to this point.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request

from .config import get_settings
from .models import CallerIdentity

_JWKS_CACHE: dict[str, dict[str, Any]] = {}  # tenant_id -> {"keys": [...], "fetched_at": float}
_JWKS_TTL_SECONDS = 3600


def _jwks_uri(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"


def _get_signing_key(tenant_id: str, kid: str) -> Any:
    # Keyed per-tenant, not a single global slot - otherwise a second tenant's request
    # within the TTL window would be checked against the first tenant's cached keys.
    now = time.monotonic()
    cached = _JWKS_CACHE.get(tenant_id)
    if cached is None or now - cached["fetched_at"] > _JWKS_TTL_SECONDS:
        try:
            resp = httpx.get(_jwks_uri(tenant_id), timeout=10.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"Could not fetch signing keys for tenant: {exc}") from exc
        cached = {"keys": resp.json()["keys"], "fetched_at": now}
        _JWKS_CACHE[tenant_id] = cached
    for key in cached["keys"]:
        if key.get("kid") == kid:
            return jwt.PyJWK.from_dict(key).key
    raise HTTPException(status_code=401, detail="Unknown signing key (kid) for caller token.")


_V1_ISSUER = "https://sts.windows.net/{tenant_id}/"
_V2_ISSUER = "https://login.microsoftonline.com/{tenant_id}/v2.0"


def _resolve_entra(request: Request) -> CallerIdentity:
    settings = get_settings()
    if not settings.entra_audience:
        # Fail closed: LDA_IDENTITY_MODE=entra with no configured audience would otherwise
        # accept a validly-signed token for *any* Entra application, not just this one.
        raise HTTPException(
            status_code=500,
            detail="Server misconfiguration: ENTRA_AUDIENCE must be set when LDA_IDENTITY_MODE=entra.",
        )

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = auth_header.split(" ", 1)[1]

    try:
        unverified_header = jwt.get_unverified_header(token)
        unverified_claims = jwt.decode(token, options={"verify_signature": False})
        tenant_id = unverified_claims.get("tid")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Token is missing a tenant id (tid) claim.")

        signing_key = _get_signing_key(tenant_id, unverified_header["kid"])
        claims = jwt.decode(
            token,
            key=signing_key,
            algorithms=["RS256"],
            audience=settings.entra_audience,
            options={"verify_aud": True},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid caller token: {exc}") from exc

    if settings.entra_tenant_id and claims.get("tid") != settings.entra_tenant_id:
        raise HTTPException(status_code=401, detail="Token issued by an unexpected tenant.")

    if settings.entra_require_issuer_match:
        expected_issuers = {
            _V1_ISSUER.format(tenant_id=tenant_id),
            _V2_ISSUER.format(tenant_id=tenant_id),
        }
        if claims.get("iss") not in expected_issuers:
            # Defense in depth beyond trusting "tid" alone: the issuer must actually match
            # the tenant the token claims to be from, not just carry a matching tid claim.
            raise HTTPException(status_code=401, detail="Token issuer does not match its claimed tenant.")

    if settings.entra_required_scope:
        scopes = (claims.get("scp") or "").split()
        if settings.entra_required_scope not in scopes:
            raise HTTPException(status_code=403, detail="Token is missing the required delegated permission.")

    if settings.entra_required_role:
        roles = claims.get("roles") or []
        if settings.entra_required_role not in roles:
            raise HTTPException(status_code=403, detail="Token is missing the required app role.")

    object_id = claims.get("oid")
    if not object_id:
        raise HTTPException(status_code=401, detail="Token is missing a user object id (oid) claim.")

    return CallerIdentity(
        tenant_id=claims["tid"],
        user_object_id=object_id,
        display_name=claims.get("name") or claims.get("preferred_username"),
    )


def _resolve_dev(request: Request) -> CallerIdentity:
    """Local-only stand-in: 'X-Debug-User: <tenant_id>:<user_object_id>[:display name]'."""
    header = request.headers.get("x-debug-user")
    if not header:
        raise HTTPException(
            status_code=401,
            detail="LDA_IDENTITY_MODE=dev requires an 'X-Debug-User: <tenant_id>:<user_object_id>' header.",
        )
    parts = header.split(":", 2)
    if len(parts) < 2:
        raise HTTPException(status_code=401, detail="X-Debug-User must be '<tenant_id>:<user_object_id>'.")
    return CallerIdentity(
        tenant_id=parts[0],
        user_object_id=parts[1],
        display_name=parts[2] if len(parts) > 2 else None,
    )


def resolve_caller(request: Request) -> CallerIdentity:
    """FastAPI dependency: returns the validated CallerIdentity for this request."""
    mode = get_settings().lda_identity_mode
    if mode == "entra":
        return _resolve_entra(request)
    if mode == "dev":
        return _resolve_dev(request)
    raise HTTPException(status_code=500, detail=f"Unknown LDA_IDENTITY_MODE '{mode}'.")
