"""Minimal MCP client for code-based executors (pattern 03 workers, pattern 04
rules calls). Hosted agents attach MCP declaratively; custom loops call it here.
Same server, two consumption styles — a deliberate teaching contrast."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .config import shared_env


def _process_result(result: Any, tool: str) -> Any:
    """Given a CallToolResult, raise on a server-side error or return the
    parsed (or raw) text. Pulled out of _call so this logic is unit-testable
    without a live MCP connection."""
    texts = [c.text for c in result.content if getattr(c, "text", None)]
    raw = texts[0] if texts else "{}"
    if getattr(result, "isError", False):
        # A server-side tool failure (bad arguments, an unhandled exception
        # in the route function, an unknown tool name) sets isError and puts
        # a message in content — found by checking CallToolResult's real
        # fields, since the previous version never looked at isError and
        # would try to json.loads() an error message as if it were the
        # tool's actual return value, silently propagating garbage instead
        # of raising.
        raise RuntimeError(f"MCP tool {tool!r} returned an error: {raw}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


async def _call(url: str, tool: str, arguments: dict) -> Any:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments=arguments)
            return _process_result(result, tool)


def call_mcp_tool(tool: str, arguments: dict) -> Any:
    """Synchronous convenience wrapper around one MCP tool call."""
    base = shared_env().get("MCP_SERVER_URL", "")
    if not base:
        raise RuntimeError(
            "MCP_SERVER_URL is empty in .shared-env — the MCP Container App was not "
            "deployed. Re-run infra/shared/deploy.sh (pass 2 needs the ACR image build "
            "to succeed).")
    url = base.rstrip("/") + "/mcp"
    return asyncio.run(_call(url, tool, arguments))
