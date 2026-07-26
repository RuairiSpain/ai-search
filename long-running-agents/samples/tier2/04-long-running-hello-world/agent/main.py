"""Protocol host for the T2 hello-world agent -- the ONLY file the hosted-
agent platform contract touches (docs/05-tier2-hosted-agents.md §2.1).

The `FoundryChatClient` / `ResponsesHostServer` wiring below is verified
against docs/05 §5.1's own reference snippet, reproduced here almost
unchanged. What's specific to this sample is `slow_then_greet`: a tool the
model is instructed to always call before answering, which is where the
~5 minutes actually happen. This models a real T2 shape -- an agent
blocked on one slow downstream call -- rather than a contrived sleep in
application code with no agent involved at all.

⚠ `ChatAgent`'s exact constructor and function-tool registration shape are
NOT verified against the installed `agent-framework-foundry` package (not
installed in this sample's dev environment) -- confirm against
`agent_framework`'s docstrings before trusting this beyond a smoke test,
same caveat docs/05 and docs/06 attach to several of their own snippets.

Deliberately does NOT emit a `gw.progress.v1` custom event anywhere -- see
the sample's README for why that's the entire point of this sample.
"""
from __future__ import annotations

import asyncio
import os

from agent_framework import ChatAgent, ai_function
from agent_framework.foundry import FoundryChatClient
from agent_framework.foundry.hosting import ResponsesHostServer  # prerelease
from azure.identity.aio import DefaultAzureCredential

SLEEP_SECONDS = int(os.environ.get("HELLO_WORLD_SLEEP_SECONDS", "300"))

# Inside the sandbox this resolves to the AGENT's managed identity
# (docs/00 §5 "What each layer proves").
credential = DefaultAzureCredential()

chat = FoundryChatClient(
    project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],  # platform-injected
    model=os.environ["FOUNDRY_MODEL_NAME"],
    credential=credential,
)


@ai_function
async def slow_then_greet() -> str:
    """Simulates ~5 minutes of real work (e.g. a slow downstream call) and
    returns a value the model can relay. Never reports partial progress --
    that omission is the whole point of this sample."""
    await asyncio.sleep(SLEEP_SECONDS)
    return "done"


hello_world_agent = ChatAgent(
    chat_client=chat,
    name="hello-world-t2",
    instructions=(
        "Always call slow_then_greet first, then reply with exactly: "
        "Hello, world! Never reply before the tool call returns."
    ),
    tools=[slow_then_greet],
)

# store=False: the hosting layer already persists conversation history
# (docs/05 §5.1) -- leaving it True would duplicate every turn into a
# store this sample doesn't have.
server = ResponsesHostServer(hello_world_agent, default_options={"store": False})
app = server.app  # listens on :8088, serves /responses + health probe
