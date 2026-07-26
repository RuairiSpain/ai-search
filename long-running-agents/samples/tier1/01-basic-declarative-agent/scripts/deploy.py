#!/usr/bin/env python3
"""Applies agents/concierge.yaml as a new agent version.

⚠ `create_version()`'s exact keyword shape is not verified against a live
project in this sample — docs/01-gateway-config-and-adapter-contract.md §3
only confirms *that* `create_version()` is the replacement for the removed
`create_agent()` in azure-ai-projects>=2.0.0, not its full signature.
Confirm against the installed package's docstring (`python -c "from
azure.ai.projects.aio import AIProjectClient; help(AIProjectClient)"`)
before trusting this in anything beyond a smoke test — the same caveat
docs/06 attaches to several T3 snippets.

Usage: python scripts/deploy.py
Requires: FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL_DEPLOYMENT, az login.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import yaml
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential

SAMPLE_ROOT = Path(__file__).resolve().parent.parent


def _resolve_env(text: str) -> str:
    """Minimal ${VAR} substitution -- mirrors gateway/config.py's contract
    (single-brace resolved client-side, double-brace left for Foundry) so
    this sample doesn't need its own substitution library."""
    for key, value in os.environ.items():
        text = text.replace(f"${{{key}}}", value)
    return text


def load_agent_definition() -> dict:
    agent_yaml = yaml.safe_load(
        _resolve_env((SAMPLE_ROOT / "agents/concierge.yaml").read_text())
    )
    instructions = (
        SAMPLE_ROOT / "agents" / agent_yaml["instructionsFile"].lstrip("./")
    ).read_text()

    return {
        "kind": agent_yaml["kind"],
        "name": agent_yaml["name"],
        "display_name": agent_yaml["displayName"],
        "description": agent_yaml["description"],
        "model": agent_yaml["model"]["deployment"],
        "model_options": agent_yaml["model"].get("options", {}),
        "instructions": instructions,
        "skills": agent_yaml.get("skills", []),
        "toolboxes": agent_yaml.get("toolboxes", []),
    }


async def main() -> None:
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    definition = load_agent_definition()

    async with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=endpoint, credential=credential
    ) as project:
        version = await project.agents.create_version(
            agent_name=definition["name"],
            body={
                "kind": definition["kind"],
                "displayName": definition["display_name"],
                "description": definition["description"],
                "model": definition["model"],
                "modelOptions": definition["model_options"],
                "instructions": definition["instructions"],
                "skills": definition["skills"],
                "toolboxes": definition["toolboxes"],
            },
        )
        print(f"deployed {definition['name']!r} -> version {version.version} (id={version.id})")


if __name__ == "__main__":
    asyncio.run(main())
