"""Artifact Broker API.

The download half of the private-storage pattern: the storage account has no
public endpoint, so this API is what a user's browser actually talks to. It
runs with a Managed Identity that has RBAC read/delete access to the private
Blob container, verifies the caller and the download token from
broker/tokens.py, and streams the blob back. Storage itself is never
directly reachable from outside the VNet.

Two ways to authorize a download - both must agree, since either alone is
forgeable or replayable on its own:
- a valid, unexpired, HMAC-signed download token scoped to this artifact_id
- the caller's own validated identity matching the artifact's recorded owner

Run standalone for local testing:
    uvicorn long_duration_agent.broker.api:app --port 8081
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from ..identity import CallerIdentity, resolve_caller
from ..observability import configure_json_logging, configure_observability, metrics_endpoint_response
from ..rate_limit import enforce_download_rate_limit
from ..storage.blob_store import get_blob_store
from ..storage.metadata_store import get_metadata_store
from .tokens import InvalidDownloadTokenError, verify_download_token

logger = logging.getLogger(__name__)

configure_json_logging()
configure_observability()

app = FastAPI(title="Artifact Broker API")


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    content, content_type = metrics_endpoint_response()
    return Response(content=content, media_type=content_type)


@app.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    token: str = Query(...),
    caller: CallerIdentity = Depends(resolve_caller),
) -> StreamingResponse:
    enforce_download_rate_limit(caller)

    try:
        token_payload = verify_download_token(token)
    except InvalidDownloadTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if token_payload.artifact_id != artifact_id:
        raise HTTPException(status_code=401, detail="Download token was not issued for this artifact.")
    if token_payload.tenant_id != caller.tenant_id or token_payload.user_object_id != caller.user_object_id:
        raise HTTPException(status_code=403, detail="This download link was not issued to you.")

    store = get_metadata_store()
    record = await store.get_artifact(artifact_id)
    if record is None or record.status != "active":
        raise HTTPException(status_code=404, detail="Artifact not found or has expired.")
    if record.tenant_id != caller.tenant_id or record.user_object_id != caller.user_object_id:
        # Defense in depth: even a forged/leaked-but-valid token can't read another user's artifact,
        # because ownership is re-checked against the authoritative record, not just the token claims.
        raise HTTPException(status_code=403, detail="You do not have access to this artifact.")

    blob_store = get_blob_store()
    try:
        stream = await blob_store.open_read_stream(record.blob_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Artifact blob is missing.") from exc

    def iter_bytes():
        try:
            while chunk := stream.read(64 * 1024):
                yield chunk
        finally:
            stream.close()

    return StreamingResponse(
        iter_bytes(),
        media_type=record.content_type,
        headers={"Content-Disposition": f'attachment; filename="{record.display_name}"'},
    )
