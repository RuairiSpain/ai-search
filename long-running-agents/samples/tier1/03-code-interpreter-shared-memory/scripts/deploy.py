#!/usr/bin/env python3
"""Deploys both agents (calculator, flashcard-formatter). See sample 01's
deploy.py for the create_version() verify-before-trusting caveat.

Usage: python scripts/deploy.py
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
    for key, value in os.environ.items():
        text = text.replace(f"${{{key}}}", value)
    return text


def load_agent_definition(name: str) -> dict:
    agent_yaml = yaml.safe_load(
        _resolve_env((SAMPLE_ROOT / f"agents/{name}.yaml").read_text())
    )
    instructions = (
        SAMPLE_ROOT / "agents" / agent_yaml["instructionsFile"].lstrip("./")
    ).read_text()
    body: dict = {
        "kind": agent_yaml["kind"],
        "displayName": agent_yaml["displayName"],
        "description": agent_yaml["description"],
        "model": agent_yaml["model"]["deployment"],
        "modelOptions": agent_yaml["model"].get("options", {}),
        "instructions": instructions,
    }
    if "tools" in agent_yaml:
        body["tools"] = agent_yaml["tools"]
    if "memory" in agent_yaml:
        body["memory"] = agent_yaml["memory"]
    if "outputSchema" in agent_yaml:
        body["outputSchema"] = agent_yaml["outputSchema"]
    return {"name": agent_yaml["name"], "body": body}


async def deploy_one(project, name: str) -> None:
    definition = load_agent_definition(name)
    version = await project.agents.create_version(
        agent_name=definition["name"], body=definition["body"]
    )
    print(f"deployed {definition['name']!r} -> version {version.version} (id={version.id})")


async def main() -> None:
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    async with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=endpoint, credential=credential
    ) as project:
        await deploy_one(project, "calculator")
        await deploy_one(project, "flashcard-formatter")


if __name__ == "__main__":
    asyncio.run(main())
