#!/usr/bin/env python3
"""No polling loop at all -- the point of this sample. Registers a push
notification config pointing at a tiny local HTTP receiver this script
starts itself, then blocks on that receiver instead of calling GetTask in
a loop. Contrast with every other T2/T3 sample's client script, which all
poll every few seconds.

Wire format (JSON-RPC SendMessage / CreateTaskPushNotificationConfig /
the X-A2A-Notification-Token header / the SSRF allowlist rejection) is
copied from tests/test_a2a_api.py::test_push_notification_config_delivers_on_completion
-- the gateway's own real, passing test for this exact flow -- not
guessed. The delivered body's shape (`{"statusUpdate": {...}}`) is
verified directly against the installed a2a-sdk: `BasePushNotificationSender`
POSTs `MessageToDict(to_stream_response(event))`, and
`a2a.utils.proto_utils.to_stream_response` wraps whichever of
Task/TaskStatusUpdateEvent/TaskArtifactUpdateEvent under one of
`task`/`statusUpdate`/`artifactUpdate` -- confirmed by constructing one by
hand and inspecting the dict it produces, not assumed from the proto
definition alone.

Usage:
    export GATEWAY_URL=http://localhost:8080
    export GATEWAY_TOKEN=$(az account get-access-token --resource api://a2a-gateway --query accessToken -o tsv)
    python client/push_demo.py
    python client/push_demo.py "say hello" --app push-hello-world-t3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

DEFAULT_APP = "push-hello-world-t3"
RECEIVER_PORT = 8899
RECEIVER_PATH = "/push"
NOTIFICATION_TOKEN = "verify-me"  # echoed back in X-A2A-Notification-Token, checked below
_TERMINAL_STATES = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}


def _fmt_elapsed(start: float) -> str:
    s = int(time.monotonic() - start)
    return f"[{s // 60:02d}:{s % 60:02d}]"


def _run_receiver(port: int, events: queue.Queue) -> ThreadingHTTPServer:
    """A real receiver still has to do SOMETHING with the request rather
    than trust it blindly -- here that's just handing it to the main
    coroutine to verify the token against. Runs in a background thread so
    the main coroutine can `await` the queue instead of blocking the whole
    process on a synchronous HTTP server."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            headers = dict(self.headers.items())
            events.put((body, headers))
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 -- BaseHTTPRequestHandler's own signature
            pass  # silence the default per-request stderr line

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _describe(body: dict) -> tuple[str | None, str | None]:
    """Returns (state, detail) for a Task- or TaskStatusUpdateEvent-shaped
    push body, else (None, None) for anything else (e.g. an artifact
    update -- this sample's own orchestration never produces one, but a
    real receiver has to handle whatever arrives, not just what it
    expects)."""
    envelope = body.get("statusUpdate") or body.get("task")
    if envelope is None:
        return None, None
    status = envelope.get("status", {})
    state = status.get("state")
    message = status.get("message")
    detail = "".join(p.get("text", "") for p in message.get("parts", [])) if message else None
    return state, detail


async def rpc(client: httpx.AsyncClient, app: str, method: str, params: dict, *, req_id: str) -> dict:
    resp = await client.post(
        f"/apps/{app}/",
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
    )
    resp.raise_for_status()
    return resp.json()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="?", default="say hello")
    parser.add_argument("--app", default=DEFAULT_APP)
    parser.add_argument("--receiver-port", type=int, default=RECEIVER_PORT)
    parser.add_argument(
        "--receiver-host",
        default="localhost",
        help="host the GATEWAY should POST push notifications to -- must resolve to this "
        "machine and be on the gateway's push_notification_allowlist",
    )
    args = parser.parse_args()

    events: queue.Queue = queue.Queue()
    server = _run_receiver(args.receiver_port, events)
    receiver_url = f"http://{args.receiver_host}:{args.receiver_port}{RECEIVER_PATH}"
    print(f"receiver listening on {receiver_url}")

    base_url = os.environ["GATEWAY_URL"]
    token = os.environ["GATEWAY_TOKEN"]

    try:
        async with httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=30.0
        ) as client:
            start = time.monotonic()
            send_body = await rpc(
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
            task_id = send_body["result"]["task"]["id"]
            print(f"{_fmt_elapsed(start)} task {task_id} submitted")

            # The deliberate failure path: L023's SSRF allowlist rejects a
            # URL whose host isn't configured -- same check
            # tests/test_a2a_api.py's own push-notification test exercises.
            blocked = await rpc(
                client,
                args.app,
                "CreateTaskPushNotificationConfig",
                {"taskId": task_id, "url": "https://not-allowlisted.evil.example/cb"},
                req_id="2",
            )
            assert "error" in blocked, "expected the SSRF allowlist to reject this URL"
            print(
                f"{_fmt_elapsed(start)} (expected) blocked non-allowlisted callback: "
                f"{blocked['error']['message']}"
            )

            ok = await rpc(
                client,
                args.app,
                "CreateTaskPushNotificationConfig",
                {"taskId": task_id, "url": receiver_url, "token": NOTIFICATION_TOKEN},
                req_id="3",
            )
            if "error" in ok:
                raise RuntimeError(f"push registration failed: {ok['error']}")
            print(f"{_fmt_elapsed(start)} registered push callback -> {receiver_url}")

            print(f"{_fmt_elapsed(start)} waiting on pushes (no GetTask polling from here on)...")
            while True:
                try:
                    body, headers = await asyncio.to_thread(events.get, True, 60.0)
                except queue.Empty:
                    raise RuntimeError("no push notification arrived within 60s") from None

                received_token = headers.get("X-A2A-Notification-Token")
                assert received_token == NOTIFICATION_TOKEN, (
                    f"token mismatch -- got {received_token!r}, expected {NOTIFICATION_TOKEN!r} "
                    "(a real receiver MUST reject on this, not just log it)"
                )
                state, detail = _describe(body)
                line = state or "(non-status push)"
                if detail:
                    line += f'  "{detail}"'
                print(f"{_fmt_elapsed(start)} PUSH  {line}  [token verified]")
                if state in _TERMINAL_STATES:
                    break
    finally:
        server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
