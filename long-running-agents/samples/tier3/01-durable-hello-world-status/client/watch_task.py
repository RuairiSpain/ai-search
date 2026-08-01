#!/usr/bin/env python3
"""Identical to ../../tier2/04-long-running-hello-world/client/watch_task.py
except for the default --app. Kept as a literal copy rather than a shared
import on purpose: the point of this sample pair is that the SAME client,
unmodified, sees a different experience purely because of what the
upstream agent narrates -- sharing the file would blur that.

Usage:
    export GATEWAY_URL=http://localhost:8080
    export GATEWAY_TOKEN=$(az account get-access-token --resource api://a2a-gateway --query accessToken -o tsv)
    python client/watch_task.py "say hello"
    python client/watch_task.py "say hello" --cancel-after 70
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
import uuid

import httpx

DEFAULT_APP = "hello-world-t3"
POLL_INTERVAL_S = 5.0


def _fmt_elapsed(start: float) -> str:
    s = int(time.monotonic() - start)
    return f"[{s // 60:02d}:{s % 60:02d}]"


async def rpc(client: httpx.AsyncClient, app: str, method: str, params: dict, *, req_id: str) -> dict:
    resp = await client.post(
        f"/apps/{app}/",
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"{method} failed: {body['error']}")
    return body["result"]


def _status_line(task: dict) -> str:
    state = task["status"]["state"]
    message = task["status"].get("message")
    if not message:
        return state
    text = "".join(p.get("text", "") for p in message.get("parts", []))
    return f"{state}  \"{text}\"" if text else state


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="?", default="say hello")
    parser.add_argument("--app", default=DEFAULT_APP)
    parser.add_argument("--cancel-after", type=float, default=None, help="seconds")
    args = parser.parse_args()

    base_url = os.environ["GATEWAY_URL"]
    token = os.environ["GATEWAY_TOKEN"]

    async with httpx.AsyncClient(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=30.0
    ) as client:
        start = time.monotonic()
        send_result = await rpc(
            client,
            args.app,
            "SendMessage",
            {
                "message": {
                    "messageId": f"m-{uuid.uuid4().hex[:8]}",
                    "role": "ROLE_USER",
                    "parts": [{"text": args.text}],
                },
                "configuration": {"returnImmediately": True},
            },
            req_id="1",
        )
        task = send_result["task"]
        task_id = task["id"]
        print(f"{_fmt_elapsed(start)} {_status_line(task)}  (task {task_id})")

        canceled = False
        req_id = 2
        while task["status"]["state"] not in (
            "TASK_STATE_COMPLETED",
            "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED",
            "TASK_STATE_REJECTED",
        ):
            await asyncio.sleep(POLL_INTERVAL_S)
            elapsed = time.monotonic() - start
            if args.cancel_after is not None and not canceled and elapsed >= args.cancel_after:
                print(f"{_fmt_elapsed(start)} --> CancelTask")
                await rpc(client, args.app, "CancelTask", {"id": task_id}, req_id=str(req_id))
                req_id += 1
                canceled = True

            get_result = await rpc(client, args.app, "GetTask", {"id": task_id}, req_id=str(req_id))
            req_id += 1
            task = get_result
            print(f"{_fmt_elapsed(start)} {_status_line(task)}")

        print(f"\nfinal state: {task['status']['state']}")


if __name__ == "__main__":
    asyncio.run(main())
