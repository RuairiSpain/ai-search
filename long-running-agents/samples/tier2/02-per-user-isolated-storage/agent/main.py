"""Protocol host for the per-user isolated-storage demo -- the ONLY file
the hosted-agent platform contract touches (docs/05-tier2-hosted-agents.md
§2.1). `FoundryChatClient` / `ResponsesAgentServerHost` imports are the
corrected, real ones (docs/08 item 16;
../../04-long-running-hello-world/agent/main.py has the same correction
and the full story of what was wrong before).

Two tools, deliberately exercising two different subsystems -- see the
sample README's "Two different sandboxes, one story" table:

- `remember_and_recall`: an `@ai_function`, pre-written Python that runs in
  THIS agent's own hosted-session container. `Path.home()` resolves inside
  the per-user session sandbox (docs/05 §5.5) -- the whole isolation demo
  is that this is the *same code and the same path* for every caller, and
  what differs is which sandbox it's running in.
- `{"type": "code_interpreter"}`: a plain dict tool definition, not a
  Python callable -- verified against the installed
  `agent-framework-foundry` package's own `_sanitize_foundry_response_tool`
  (`agent_framework_foundry/_tools.py`), which explicitly handles
  dict-shaped hosted tool definitions including injecting a default
  `{"type": "auto"}` container for `code_interpreter` when one isn't
  supplied. Mixing a function tool and a hosted tool in the same `tools=`
  list is exactly what that sanitizer exists to support.

⚠ `ChatAgent`'s exact constructor and multi-tool-type handling are NOT
fully verified end-to-end (no live Foundry project in this sample's dev
environment) -- the code_interpreter tool shape above is grounded in the
installed package's source, not guessed, but that's a weaker bar than
"actually run against a live agent." Same caveat class as
../../04-long-running-hello-world/agent/main.py.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from agent_framework import ChatAgent, ai_function
from agent_framework.foundry import FoundryChatClient
from azure.ai.agentserver.responses.hosting import ResponsesAgentServerHost
from azure.identity.aio import DefaultAzureCredential

NOTES_PATH = Path.home() / "session_notes.jsonl"

credential = DefaultAzureCredential()

chat = FoundryChatClient(
    project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],  # platform-injected
    model=os.environ["FOUNDRY_MODEL_NAME"],
    credential=credential,
)


@ai_function
async def remember_and_recall(message_text: str) -> dict:
    """Appends `message_text` to this session's private notes file and
    returns the full history plus a turn number.

    `NOTES_PATH` is a FIXED path (`Path.home() / "session_notes.jsonl"`) --
    identical for every caller. Whether two different users' calls ever see
    each other's notes depends entirely on whether they're running inside
    the same `$HOME`, which is exactly the platform isolation property this
    sample exists to demonstrate: with `identity: per_user` on the gateway
    upstream config, they never are (docs/00 §5).
    """
    notes: list[str] = []
    if NOTES_PATH.exists():
        notes = [json.loads(line) for line in NOTES_PATH.read_text().splitlines() if line.strip()]
    notes.append(message_text)
    NOTES_PATH.write_text("\n".join(json.dumps(n) for n in notes) + "\n")
    return {
        "home_path": str(NOTES_PATH),
        "turn_number": len(notes),
        "all_notes_this_session": notes,
    }


isolated_storage_agent = ChatAgent(
    chat_client=chat,
    name="isolated-storage-t2",
    instructions=(Path(__file__).parent / "instructions.md").read_text(),
    tools=[remember_and_recall, {"type": "code_interpreter"}],
)

# store=False: the hosting layer already persists conversation history
# (docs/05 §5.1) -- leaving it True would duplicate every turn into a
# store this sample doesn't have.
server = ResponsesAgentServerHost(isolated_storage_agent, default_options={"store": False})
app = server.app  # listens on :8088, serves /responses + health probe
