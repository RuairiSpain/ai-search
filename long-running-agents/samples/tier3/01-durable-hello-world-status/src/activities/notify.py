"""Pushes a status event to the gateway's webhook. An activity, not
orchestrator code, so it's checkpointed and retried by the platform and the
orchestrator itself stays free of I/O (docs/06-tier3-durable-agents.md
§5.4 -- this is that section's own `notify` snippet, adapted to import
`httpx` instead of an unspecified `http` and to point at this sample's own
task, and verified directly against `ProgressPayload` in
src/gateway/api/webhooks.py rather than assumed to match it.
"""
from __future__ import annotations

import os

import httpx

from orchestrations.hello_world import bp

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
