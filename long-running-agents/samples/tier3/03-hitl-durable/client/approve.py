#!/usr/bin/env python3
"""Two modes, matching the two things a human does with a multi-day HITL
task: start one, and answer one.

Wire format (JSON-RPC SendMessage/GetTask, `configuration.returnImmediately`)
copied from ../../01-durable-hello-world-status/client/watch_task.py, same
`/apps/{app}/` RPC endpoint convention. Not shared as an import -- same
reasoning as that file's own docstring: each sample stays independently
runnable from its own directory.

Usage:
    export GATEWAY_URL=http://localhost:8080
    export GATEWAY_TOKEN=$(az account get-access-token --resource api://a2a-gateway --query accessToken -o tsv)

    # Start a request, polls until it pauses for approval (or finishes):
    python client/approve.py "Client dinner, $85"

    # The deliberate failure path -- a 20-second deadline instead of 14 days:
    python client/approve.py "Client dinner, $85" --timeout-seconds 20

    # Answer it (the activity log / request_approval.py tells you the
    # exact task_id to use):
    python client/approve.py <task_id> --decision approved
    python client/approve.py <task_id> --decision rejected --reason "over budget"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid

import httpx

DEFAULT_APP = "expense-approval-t3"
POLL_INTERVAL_S = 3.0
# TASK_STATE_INPUT_REQUIRED is deliberately NOT in this set -- reaching it
# is the expected pause point for a `request`, not something to keep
# polling past. A `respond` call polls past it once (the reply may itself
# pause again, or finish, or -- if this reply lost the race to the
# deadline timer -- land on an already-expired task instead).
_STOP_STATES = (
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
)


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
    return f'{state}  "{text}"' if text else state


async def _poll_until(
    client: httpx.AsyncClient, app: str, task: dict, start: float, *, stop_also_on_input_required: bool
) -> dict:
    req_id = 2
    while task["status"]["state"] not in _STOP_STATES:
        if stop_also_on_input_required and task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
            break
        await asyncio.sleep(POLL_INTERVAL_S)
        task = await rpc(client, app, "GetTask", {"id": task["id"]}, req_id=str(req_id))
        req_id += 1
        print(f"{_fmt_elapsed(start)} {_status_line(task)}")
    return task


async def cmd_request(client: httpx.AsyncClient, app: str, text: str, timeout_seconds: float | None) -> None:
    payload = {"expense": text}
    if timeout_seconds is not None:
        payload["timeout_seconds"] = timeout_seconds
    start = time.monotonic()
    send_result = await rpc(
        client,
        app,
        "SendMessage",
        {
            "message": {
                "messageId": f"m-{uuid.uuid4().hex[:8]}",
                "role": "ROLE_USER",
                "parts": [{"text": json.dumps(payload)}],
            },
            "configuration": {"returnImmediately": True},
        },
        req_id="1",
    )
    task = send_result["task"]
    print(f"{_fmt_elapsed(start)} {_status_line(task)}  (task {task['id']})")
    task = await _poll_until(client, app, task, start, stop_also_on_input_required=True)

    if task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
        print("\nPaused for approval. Answer with:")
        print(f'  python client/approve.py {task["id"]} --decision approved')
        print(f'  python client/approve.py {task["id"]} --decision rejected --reason "..."')
        if timeout_seconds is not None:
            print(f"(this one expires in ~{timeout_seconds:.0f}s if nobody answers)")
    else:
        print(f"\nfinal state: {task['status']['state']}")


async def cmd_respond(client: httpx.AsyncClient, app: str, task_id: str, decision: str, reason: str | None) -> None:
    payload = {"decision": decision}
    if reason:
        payload["reason"] = reason
    if decision == "approved":
        payload["approved_by"] = os.environ.get("USER", "sample-user")

    start = time.monotonic()
    await rpc(
        client,
        app,
        "SendMessage",
        {
            "message": {
                "messageId": f"m-{uuid.uuid4().hex[:8]}",
                "taskId": task_id,
                "role": "ROLE_USER",
                "parts": [{"text": json.dumps(payload)}],
            },
            "configuration": {"returnImmediately": True},
        },
        req_id="1",
    )
    task = await rpc(client, app, "GetTask", {"id": task_id}, req_id="2")
    print(f"{_fmt_elapsed(start)} {_status_line(task)}")
    task = await _poll_until(client, app, task, start, stop_also_on_input_required=False)
    print(f"\nfinal state: {task['status']['state']}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text_or_task_id", help="expense description (new request) or task id (--decision)")
    parser.add_argument("--app", default=DEFAULT_APP)
    parser.add_argument("--decision", choices=["approved", "rejected"], default=None)
    parser.add_argument("--reason", default=None, help="required in spirit, not enforced, for --decision rejected")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="override the 14-day default deadline -- use a small value to see the timeout path",
    )
    args = parser.parse_args()

    base_url = os.environ["GATEWAY_URL"]
    token = os.environ["GATEWAY_TOKEN"]

    async with httpx.AsyncClient(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=30.0
    ) as client:
        if args.decision is not None:
            await cmd_respond(client, args.app, args.text_or_task_id, args.decision, args.reason)
        else:
            await cmd_request(client, args.app, args.text_or_task_id, args.timeout_seconds)


if __name__ == "__main__":
    asyncio.run(main())
