"""Protocol host for the T2 hello-world agent -- the ONLY file the hosted-
agent platform contract touches (docs/05-tier2-hosted-agents.md §2.1).

`FoundryChatClient` and `ResponsesAgentServerHost` are imported from their
real, verified locations -- `agent_framework.foundry` and
`azure.ai.agentserver.responses.hosting` respectively. An earlier version
of this file (and of docs/05 §5.1's own reference snippet) imported
`ResponsesHostServer` from `agent_framework.foundry.hosting`, which does
not exist in the real, installed `agent-framework-foundry` package --
confirmed by downloading and inspecting it directly. See
docs/08-open-items-and-experiments.md item 16 for the full account.

What's specific to this sample is `slow_then_greet`: a tool the model is
instructed to always call before answering, which is where the ~5 minutes
actually happen. This models a real T2 shape -- an agent blocked on one
slow downstream call -- rather than a contrived sleep in application code
with no agent involved at all. It also happens to be exactly the shape
`FoundryResponsesAdapter.follow()`'s narration mechanism narrates: one
`function_call` output item, in progress for the whole run -- see the
sample's README "What you'll actually see".

⚠ `ChatAgent`'s exact constructor and function-tool registration shape,
and `ResponsesAgentServerHost`'s exact constructor/`.app` attribute, are
NOT verified against the installed packages (not installed in this
sample's dev environment) -- confirm against their docstrings before
trusting this beyond a smoke test, same caveat docs/05 and docs/06 attach
to several of their own snippets. Only the import paths and class names
are confirmed real.

This agent emits no progress signal of its own -- narration for it comes
entirely from the gateway's `_narrate()` reading the platform's own
`Response.output`, not from anything in this file. See the sample's
README for what that means for the resulting trace.
"""
from __future__ import annotations

import asyncio
import os

from agent_framework import ChatAgent, ai_function
from agent_framework.foundry import FoundryChatClient
from azure.ai.agentserver.responses.hosting import ResponsesAgentServerHost
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
    returns a value the model can relay. Reports no *internal* progress of
    its own -- the gateway's narration for this tool call is one static
    line ("running tool: slow_then_greet") for its whole duration, since
    that's all a `function_call` output item's boundary tells the platform."""
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
server = ResponsesAgentServerHost(hello_world_agent, default_options={"store": False})
app = server.app  # listens on :8088, serves /responses + health probe
