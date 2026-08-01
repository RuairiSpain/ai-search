#!/usr/bin/env python3
"""Drives calculator -> flashcard-formatter directly against the Responses
API, both calls against the SAME conversation -- the reference for what
"shared history and memory" means at the wire level, mirroring
../../08-workflow-executor-reviewer/scripts/run.py's isolation reference in
the opposite direction.

Usage:
    python scripts/run.py "What is 847 divided by 11, rounded to 2 decimal places?"
    python scripts/run.py "What is 12 divided by 0?"   # the deliberate failure path
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential

CALCULATOR = "calculator"
FORMATTER = "flashcard-formatter"


async def ask(openai_client, *, agent_name: str, conversation_id: str, text: str) -> str:
    resp = await openai_client.responses.create(
        background=False,
        conversation=conversation_id,
        input=text,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
    )
    return resp.output_text


async def run(project, question: str) -> dict:
    calc_client = project.get_openai_client(agent_name=CALCULATOR)
    fmt_client = project.get_openai_client(agent_name=FORMATTER)

    # ONE conversation, used by both agents -- the deliberate opposite of
    # sample 08's fresh-conversation-per-reviewer-call pattern.
    conversation = await calc_client.conversations.create(metadata={"sample": "t1-calc-to-flashcard"})

    calc_result = await ask(
        calc_client, agent_name=CALCULATOR, conversation_id=conversation.id, text=question
    )
    print(f"--- calculator ---\n{calc_result}\n")

    flashcard_text = await ask(
        fmt_client,
        agent_name=FORMATTER,
        conversation_id=conversation.id,  # same id -- formatter sees calc_result and `question` both
        text="Format the calculation above as an ELI5 flashcard.",
    )
    try:
        return json.loads(flashcard_text)
    except json.JSONDecodeError:
        # outputSchema is a contract on the agent's structured output, not
        # a hard runtime guarantee this sample enforces client-side --
        # surface the raw text rather than crashing if a model deviates.
        return {"raw": flashcard_text}


async def main() -> None:
    question = " ".join(sys.argv[1:]) or "What is 847 divided by 11, rounded to 2 decimal places?"
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    async with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=endpoint, credential=credential
    ) as project:
        flashcard = await run(project, question)
        print("--- flashcard ---")
        print(json.dumps(flashcard, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
