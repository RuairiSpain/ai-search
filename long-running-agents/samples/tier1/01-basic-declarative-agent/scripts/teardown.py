#!/usr/bin/env python3
"""Deletes the agent version this sample created. T1 has no session/sandbox
to tear down (docs/00 §3 "What each tier does not have") -- the version
itself is the only durable resource deploy.py minted.

⚠ `delete_version()`'s exact signature has the same verify-before-trusting
caveat as create_version() in deploy.py.

Usage: python scripts/teardown.py [--version N]   # omit to delete all versions
"""
from __future__ import annotations

import asyncio
import os
import sys

from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential

AGENT_NAME = "concierge"


async def main() -> None:
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    only_version = None
    if "--version" in sys.argv:
        only_version = int(sys.argv[sys.argv.index("--version") + 1])

    async with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=endpoint, credential=credential
    ) as project:
        versions = [
            v async for v in project.agents.list_versions(agent_name=AGENT_NAME)
        ]
        if only_version is not None:
            versions = [v for v in versions if v.version == only_version]

        if not versions:
            print(f"no versions of {AGENT_NAME!r} to delete")
            return

        for v in versions:
            await project.agents.delete_version(agent_name=AGENT_NAME, version=v.version)
            print(f"deleted {AGENT_NAME!r} version {v.version}")


if __name__ == "__main__":
    asyncio.run(main())
