#!/usr/bin/env python3
"""Drives the executor/reviewer loop directly against the Responses API --
the reference for what "the reviewer has no history" means at the wire
level (see README "How isolation is achieved").

The executor keeps ONE conversation across every round, so it can be asked
to revise its own draft. The reviewer gets a BRAND NEW conversation every
single round -- created fresh, used once, never reused -- so nothing about
round 2's review can be influenced by round 1 ever having happened. Compare
with ../../03-code-interpreter-shared-memory/scripts/run.py, where the
second agent deliberately reuses the first agent's conversation.

Usage:
    python scripts/run.py "Ticket: customer wants a refund for a damaged item, order #A-4471."
    python scripts/run.py "..." --max-rounds 1   # exercise the deliberate failure path
"""
from __future__ import annotations

import asyncio
import os
import sys

from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential

EXECUTOR = "executor"
REVIEWER = "reviewer"


async def ask(openai_client, *, agent_name: str, conversation_id: str, text: str) -> str:
    resp = await openai_client.responses.create(
        background=False,
        conversation=conversation_id,
        input=text,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
    )
    return resp.output_text


async def run_loop(project, ticket: str, *, max_rounds: int) -> str:
    executor_client = project.get_openai_client(agent_name=EXECUTOR)
    reviewer_client = project.get_openai_client(agent_name=REVIEWER)

    executor_conv = await executor_client.conversations.create(metadata={"sample": "t1-review-loop"})

    feedback: str | None = None
    for round_num in range(1, max_rounds + 1):
        executor_input = ticket if feedback is None else f"{ticket}\n\n{feedback}"
        draft = await ask(
            executor_client, agent_name=EXECUTOR, conversation_id=executor_conv.id, text=executor_input
        )
        print(f"--- round {round_num}: executor draft ---\n{draft}\n")

        # A fresh conversation every round -- this is the isolation the
        # sample exists to demonstrate. The reviewer never sees `ticket`,
        # never sees `feedback`, never sees round 1's draft once round 2
        # starts. Only `draft` crosses the boundary.
        reviewer_conv = await reviewer_client.conversations.create(
            metadata={"sample": "t1-review-loop", "round": str(round_num)}
        )
        review = await ask(
            reviewer_client, agent_name=REVIEWER, conversation_id=reviewer_conv.id, text=draft
        )
        print(f"--- round {round_num}: reviewer verdict ---\n{review}\n")

        if "[REWORK]" not in review.upper():
            return f"APPROVED after {round_num} round(s):\n\n{draft}"
        feedback = review

    # Deliberate failure path -- see README.
    return f"NEEDS_INPUT: reviewer did not approve after {max_rounds} round(s). Last feedback: {feedback}"


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    max_rounds = 3
    if "--max-rounds" in sys.argv:
        max_rounds = int(sys.argv[sys.argv.index("--max-rounds") + 1])
    ticket = " ".join(args) or "Ticket: customer wants a refund for a damaged item, order #A-4471."

    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    async with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=endpoint, credential=credential
    ) as project:
        result = await run_loop(project, ticket, max_rounds=max_rounds)
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
