"""T3 push receiver. The T3 A2A server does not stream — it posts status
and artifact events to this callback (docs/06-tier3-durable-agents.md
§4.1, §5.4). Writes land in gw_event, which the T3 adapter's follow()
then relays to whichever gateway replica is holding the client's SSE
connection, via LISTEN/NOTIFY (docs/03-postgres-schema.md).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from gateway.store.task_store import TaskStore


class ProgressPayload(BaseModel):
    schema_: str = "gw.progress.v1"
    task_id: str
    kind: str  # "status" | "artifact"
    sequence: int
    payload: dict


def build_webhook_router(tasks: TaskStore) -> APIRouter:
    router = APIRouter()

    @router.post("/tasks/{task_id}/events")
    async def receive_event(
        task_id: str, body: ProgressPayload, authorization: str | None = Header(default=None)
    ) -> dict:
        _verify_callback_token(authorization)
        if body.task_id != task_id:
            raise HTTPException(400, "task_id mismatch between path and body")
        await tasks.append_event(task_id, body.sequence, body.kind, body.payload)
        return {"ok": True}

    return router


def _verify_callback_token(authorization: str | None) -> None:
    """Placeholder shared-secret check. Replace with proper Entra/Functions
    key validation before this leaves a dev environment — the callback
    endpoint accepts writes into another user's task stream if forged."""
    expected = os.environ.get("GATEWAY_CALLBACK_TOKEN")
    if not expected:
        return  # local dev with no token configured
    if authorization != f"Bearer {expected}":
        raise HTTPException(401)
