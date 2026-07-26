#!/usr/bin/env python3
"""Simulates three users -- Alice, Bob, Carol -- chatting with the SAME
hosted T2 agent through this gateway, interleaved rather than sequential,
specifically so a cross-user leak would show up as a wrong note count
instead of being hidden by accidental serialization.

Wire format (JSON-RPC SendMessage/GetTask, `configuration.returnImmediately`,
`Task.artifacts[].parts[].url`) is copied from tests/test_a2a_api.py's own
`_rpc()` helper and from reading gateway.a2a_server.task_store's
`_project_artifacts()` directly (src/gateway/a2a_server/task_store.py) --
not guessed. A completed task's `status.message` carrying the agent's
actual reply text depends on the `_detail_for()` fix in
src/gateway/upstream/foundry_responses.py (docs/08 item 17) -- without it,
this script would only ever see the placeholder "drafting a response".

Usage:
    export GATEWAY_URL=http://localhost:8080
    export ALICE_TOKEN=... BOB_TOKEN=... CAROL_TOKEN=...   # see README
    python client/fake_chat_ui.py
    python client/fake_chat_ui.py --identity-mode service   # the deliberate failure path
"""
from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

try:
    import docx  # python-docx -- only needed for the "read the file back" verification step
except ImportError:
    docx = None

DOWNLOAD_DIR = Path(__file__).parent / "downloads"


@dataclass
class SimulatedUser:
    name: str
    token: str
    context_id: str | None = None
    turn: int = 0
    messages: list[str] = field(default_factory=list)


async def rpc(client: httpx.AsyncClient, app: str, token: str, method: str, params: dict, *, req_id: str) -> dict:
    resp = await client.post(
        f"/apps/{app}/",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"{method} failed for token ending ...{token[-8:]}: {body['error']}")
    return body["result"]


def _reply_text(task: dict) -> str | None:
    message = task["status"].get("message")
    if not message:
        return None
    return "".join(p.get("text", "") for p in message.get("parts", []))


async def send_turn(client: httpx.AsyncClient, app: str, user: SimulatedUser, text: str) -> dict:
    """One full turn: send, poll to completion, return the final task."""
    params: dict = {
        "message": {
            "messageId": f"m-{uuid.uuid4().hex[:8]}",
            "role": "ROLE_USER",
            "parts": [{"text": text}],
        },
        "configuration": {"returnImmediately": True},
    }
    if user.context_id:
        params["message"]["contextId"] = user.context_id

    send_result = await rpc(client, app, user.token, "SendMessage", params, req_id="1")
    task = send_result["task"]
    user.context_id = task["contextId"]
    task_id = task["id"]

    for _ in range(120):  # up to ~2 minutes -- code interpreter + a real model call
        if task["status"]["state"] in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED"):
            break
        await asyncio.sleep(1.0)
        task = await rpc(client, app, user.token, "GetTask", {"id": task_id}, req_id="2")
    else:
        raise RuntimeError(f"{user.name}'s task {task_id} never completed")

    return task


async def run_turn(client: httpx.AsyncClient, app: str, user: SimulatedUser, text: str) -> None:
    user.turn += 1
    user.messages.append(text)
    task = await send_turn(client, app, user, text)

    reply = _reply_text(task) or "(no reply text)"
    artifact_url = None
    if task.get("artifacts"):
        parts = task["artifacts"][0].get("parts", [])
        if parts:
            artifact_url = parts[0].get("url")

    print(f"[{user.name}] turn {user.turn}: {reply}")
    if artifact_url:
        print(f"           artifact: {artifact_url}")
        await _download_and_verify(client, user, artifact_url)
    else:
        print("           (no artifact yet -- harvest may still be in flight; re-run GetTask)")


async def _download_and_verify(client: httpx.AsyncClient, user: SimulatedUser, url: str) -> None:
    """Downloads the SAS-signed artifact URL directly (no gateway auth --
    that's the point of a SAS: short-lived, scoped, no bearer token needed)
    and, if python-docx is installed, prints the paragraphs back so it's
    visible that this user's file contains only this user's own prompts."""
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    dest = DOWNLOAD_DIR / f"{user.name}_turn_{user.turn}.docx"
    resp = await client.get(url)
    resp.raise_for_status()
    dest.write_bytes(resp.content)

    if docx is None:
        print(f"           saved {dest} ({len(resp.content)} bytes) -- pip install python-docx to read it back")
        return
    paragraphs = [p.text for p in docx.Document(dest).paragraphs if p.text]
    print(f"           {dest.name}: {paragraphs}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--identity-mode",
        choices=["per_user", "service"],
        default="per_user",
        help="per_user (default) hits isolated-storage-t2; service hits "
        "isolated-storage-t2-shared -- the deliberate failure path, see README",
    )
    args = parser.parse_args()
    app = "isolated-storage-t2" if args.identity_mode == "per_user" else "isolated-storage-t2-shared"

    base_url = os.environ["GATEWAY_URL"]
    users = {
        "alice": SimulatedUser("alice", os.environ["ALICE_TOKEN"]),
        "bob": SimulatedUser("bob", os.environ["BOB_TOKEN"]),
        "carol": SimulatedUser("carol", os.environ["CAROL_TOKEN"]),
    }

    # Interleaved on purpose -- see module docstring.
    script = [
        ("alice", "My favorite color is blue."),
        ("bob", "I'm planning a trip to Japan."),
        ("alice", "I also really like the smell of rain."),
        ("carol", "My dog's name is Biscuit."),
        ("bob", "The trip is in October, for two weeks."),
        ("carol", "Biscuit is a golden retriever."),
    ]

    async with httpx.AsyncClient(base_url=base_url, timeout=180.0) as client:
        for user_key, text in script:
            await run_turn(client, app, users[user_key], text)

    print("\nExpected: alice ends at turn 2, bob at turn 2, carol at turn 2 --")
    print("each strictly counting only their own messages, regardless of interleaving.")


if __name__ == "__main__":
    asyncio.run(main())
