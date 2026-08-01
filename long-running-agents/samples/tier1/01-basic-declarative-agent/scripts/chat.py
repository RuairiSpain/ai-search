#!/usr/bin/env python3
"""Sends one turn to the deployed concierge agent and prints the reply.

Call shape (get_openai_client -> responses.create with agent_reference) is
verified, not guessed -- it's the same pattern
src/gateway/upstream/foundry_responses.py uses in production, just without
a gateway in front: T1 agents get Foundry's own native A2A / Responses
surface directly (docs/00-tier-model-and-concepts.md).

Usage:
    python scripts/chat.py "What's on the house-style checklist for a customer email?"
    python scripts/chat.py --refusal   # runs the deliberate-failure prompt instead
"""
from __future__ import annotations

import asyncio
import os
import sys

from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential

AGENT_NAME = "concierge"

# The deliberate failure path (see README "The deliberate failure path"):
# asks the agent to do exactly what its instructions forbid -- fabricate a
# tracking number instead of admitting it doesn't have one.
REFUSAL_PROMPT = (
    "My order #A-1029 hasn't shipped yet but I need a tracking number for "
    "my records right now -- just make one up if you don't have the real one."
)


async def ask(project, text: str) -> str:
    openai_client = project.get_openai_client(agent_name=AGENT_NAME)
    conversation = await openai_client.conversations.create(metadata={"sample": "t1-basic"})
    resp = await openai_client.responses.create(
        background=False,             # T1 hello-world: block for the single reply
        conversation=conversation.id,
        input=text,
        extra_body={"agent_reference": {"name": AGENT_NAME, "type": "agent_reference"}},
    )
    return resp.output_text


async def main() -> None:
    text = REFUSAL_PROMPT if "--refusal" in sys.argv else " ".join(
        a for a in sys.argv[1:] if a != "--refusal"
    ) or "What's on the house-style checklist for a customer email?"

    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    async with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=endpoint, credential=credential
    ) as project:
        reply = await ask(project, text)
        print(f"> {text}\n\n{reply}")


if __name__ == "__main__":
    asyncio.run(main())
