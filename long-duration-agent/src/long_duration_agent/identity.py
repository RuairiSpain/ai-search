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

_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 3600


def _jwks_uri(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"


def _get_signing_key(tenant_id: str, kid: str) -> Any:
    now = time.monotonic()
    if _JWKS_CACHE["keys"] is None or now - _JWKS_CACHE["fetched_at"] > _JWKS_TTL_SECONDS:
        resp = httpx.get(_jwks_uri(tenant_id), timeout=10.0)
        resp.raise_for_status()
        _JWKS_CACHE["keys"] = resp.json()["keys"]
        _JWKS_CACHE["fetched_at"] = now
    for key in _JWKS_CACHE["keys"]:
        if key.get("kid") == kid:
            return jwt.PyJWK.from_dict(key).key
    raise HTTPException(status_code=401, detail="Unknown signing key (kid) for caller token.")


def _resolve_entra(request: Request) -> CallerIdentity:
    settings = get_settings()
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
            audience=settings.entra_audience or None,
            options={"verify_aud": bool(settings.entra_audience)},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid caller token: {exc}") from exc

    if settings.entra_tenant_id and claims.get("tid") != settings.entra_tenant_id:
        raise HTTPException(status_code=401, detail="Token issued by an unexpected tenant.")

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
