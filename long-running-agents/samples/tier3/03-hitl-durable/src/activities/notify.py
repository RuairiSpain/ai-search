"""Pushes a status event to the gateway's webhook. Literal copy of
../../01-durable-hello-world-status/src/activities/notify.py -- same
contract (`ProgressPayload` in src/gateway/api/webhooks.py), same reason
to duplicate rather than share (samples stay independently runnable). An
activity, not orchestrator code, so it's checkpointed and retried by the
platform and the orchestrator itself stays free of I/O
(docs/06-tier3-durable-agents.md §5.4).
"""
from __future__ import annotations

import os

import httpx

from orchestrations.approval import bp

GATEWAY_CALLBACK = os.environ["GATEWAY_CALLBACK_URL"]  # e.g. http://gateway:8080/webhooks
GATEWAY_CALLBACK_TOKEN = os.environ.get("GATEWAY_CALLBACK_TOKEN")  # matches webhooks.py's shared-secret check


@bp.activity_trigger(input_name="payload")
async def notify(payload: dict) -> None:
    headers = {}
    if GATEWAY_CALLBACK_TOKEN:
        headers["Authorization"] = f"Bearer {GATEWAY_CALLBACK_TOKEN}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{GATEWAY_CALLBACK}/tasks/{payload['task_id']}/events",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
