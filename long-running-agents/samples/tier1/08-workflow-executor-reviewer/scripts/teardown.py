#!/usr/bin/env python3
"""Deletes every version of both agents. See sample 01's teardown.py for
the delete_version() verify-before-trusting caveat.

Usage: python scripts/teardown.py
"""
from __future__ import annotations

import asyncio
import os

from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential

AGENT_NAMES = ["executor", "reviewer"]


async def teardown_one(project, agent_name: str) -> None:
    versions = [v async for v in project.agents.list_versions(agent_name=agent_name)]
    if not versions:
        print(f"no versions of {agent_name!r} to delete")
        return
    for v in versions:
        await project.agents.delete_version(agent_name=agent_name, version=v.version)
        print(f"deleted {agent_name!r} version {v.version}")


async def main() -> None:
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    async with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=endpoint, credential=credential
    ) as project:
        for name in AGENT_NAMES:
            await teardown_one(project, name)


if __name__ == "__main__":
    asyncio.run(main())
