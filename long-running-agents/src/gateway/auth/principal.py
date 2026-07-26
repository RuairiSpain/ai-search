"""Principal extraction and validation.

This is the ONLY place a user identity enters the system. Nothing else may
read a user id from a request body, query string or custom header — see
docs/05-tier2-hosted-agents.md §3.3 "The anti-pattern this replaces".

The resulting Principal.subject is used for three independent purposes:
  1. gw_context.principal_subject   -- the gateway's own authorisation boundary
  2. x-ms-user-identity              -- Foundry per-user sandbox delegation (T2)
  3. prompt_cache_key / safety_identifier on every Responses call (T1/T2, D1)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

# Charset accepted by x-ms-user-identity: 1-256 chars, letters, digits, and
# . _ : - @   Reject rather than sanitise: a mangled identifier that
# normalises onto another user's value is a silent cross-user data leak.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,256}$")


class AuthError(Exception):
    """Always surfaces as 401. Never leak which check failed."""


@dataclass(frozen=True)
class Principal:
    """Resolved from the verified inbound token. NEVER from client-supplied data."""

    subject: str  # "{tid}.{oid}" — globally unique, immutable
    tenant: str

    def user_identity_header(self) -> str:
        if not _USER_ID_RE.fullmatch(self.subject):
            # Should be unreachable if EntraValidator built this Principal,
            # but the header value is security-critical enough to re-check
            # at the point of use rather than trust construction alone.
            raise AuthError("principal is not a valid x-ms-user-identity")
        return self.subject


class EntraValidator:
    """Validates the inbound bearer token from the chat UI.

    Audience is the GATEWAY's own app registration, never
    https://ai.azure.com — the client authenticates to us, not to Foundry
    (docs/05-tier2-hosted-agents.md §3.2).
    """

    def __init__(self, tenant_id: str, audience: str, subject_claim: str = "oid"):
        self._jwks = PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
            cache_keys=True,
        )
        self._issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self._audience = audience
        self._subject_claim = subject_claim

    def principal_from(self, authorization: str | None) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthError("missing bearer token")
        token = authorization[7:]

        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],  # never accept "none" or HS*
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError("token validation failed") from exc

        oid = claims.get(self._subject_claim)
        tid = claims.get("tid")
        if not oid or not tid:
            raise AuthError("token missing required claims")

        # oid is unique within a tenant, not globally. Qualify it, or two
        # users in different tenants can collide onto one sandbox.
        subject = f"{tid}.{oid}"
        if not _USER_ID_RE.fullmatch(subject):
            raise AuthError("resolved principal is not a valid x-ms-user-identity")
        return Principal(subject=subject, tenant=tid)
