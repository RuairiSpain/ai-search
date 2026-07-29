"""Short-lived, single-artifact download tokens.

Because the storage account has public network access disabled, a browser
can never be handed a raw Azure Blob SAS URL - there is no public endpoint
for it to hit. Instead the link returned to the chat UI points at the
Artifact Broker API (broker/api.py), which runs with a Managed Identity that
*can* reach the private blob, and authorizes each request with one of these
tokens.

The token is an HMAC-signed, self-contained credential (artifact_id + owner
+ expiry), analogous in spirit to a SAS but scoped to our own broker rather
than to Blob Storage directly:

- Freshly minted on every request (workflow completion, or a future
  "get me a new link" call) - a 15 minute TTL, matching the user's
  requirement of always issuing a new one rather than reusing/persisting it.
- Never written to the metadata store or logs.
- Useless without also passing the ownership check server-side (the token
  proves "this link was issued for artifact X owned by user Y", not "let
  anyone in" - see broker/api.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

from ..config import get_settings


class InvalidDownloadTokenError(ValueError):
    pass


@dataclass(frozen=True)
class DownloadTokenPayload:
    artifact_id: str
    tenant_id: str
    user_object_id: str
    expires_at_epoch: int


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _sign(payload_b64: str) -> str:
    from ..secrets import get_broker_signing_key

    key = get_broker_signing_key().encode("utf-8")
    digest = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def issue_download_token(*, artifact_id: str, tenant_id: str, user_object_id: str) -> tuple[str, int]:
    """Returns (token, expires_at_epoch_seconds)."""
    ttl_minutes = get_settings().lda_download_token_ttl_minutes
    expires_at = int(time.time()) + ttl_minutes * 60
    payload = {
        "artifact_id": artifact_id,
        "tenant_id": tenant_id,
        "user_object_id": user_object_id,
        "exp": expires_at,
        # Random per issuance so two tokens minted in the same second for the same
        # artifact never collide - each download link is its own, independent grant.
        "jti": _b64url_encode(uuid.uuid4().bytes),
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(payload_b64)
    return f"{payload_b64}.{signature}", expires_at


def verify_download_token(token: str) -> DownloadTokenPayload:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError as exc:
        raise InvalidDownloadTokenError("Malformed download token.") from exc

    expected_signature = _sign(payload_b64)
    if not hmac.compare_digest(signature, expected_signature):
        raise InvalidDownloadTokenError("Download token signature does not match.")

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidDownloadTokenError("Download token payload is not valid.") from exc

    if int(time.time()) > int(payload["exp"]):
        raise InvalidDownloadTokenError("Download token has expired.")

    return DownloadTokenPayload(
        artifact_id=payload["artifact_id"],
        tenant_id=payload["tenant_id"],
        user_object_id=payload["user_object_id"],
        expires_at_epoch=payload["exp"],
    )


def build_download_link(*, artifact_id: str, tenant_id: str, user_object_id: str) -> tuple[str, datetime]:
    """Mints a fresh token and returns (broker_download_url, expires_at)."""
    token, expires_at_epoch = issue_download_token(
        artifact_id=artifact_id, tenant_id=tenant_id, user_object_id=user_object_id
    )
    base_url = get_settings().lda_broker_base_url.rstrip("/")
    url = f"{base_url}/artifacts/{quote(artifact_id)}/download?token={quote(token)}"
    expires_at = datetime.fromtimestamp(expires_at_epoch, tz=timezone.utc)
    return url, expires_at
