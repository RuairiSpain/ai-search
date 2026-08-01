"""MCP server core: JSON-RPC 2.0 dispatch over the MCP method set.

Dependency-free (stdlib + PyYAML via patcomp). Implements initialize,
tools/list, tools/call, ping. Transport-agnostic: transport.py feeds it decoded
messages and writes back whatever it returns.
"""
from __future__ import annotations

import json
from typing import Any

from . import tools

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "patcomp-mcp", "version": "0.1.0"}

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _ok(id_: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_: Any, code: int, message: str, data: Any = None) -> dict:
    e = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": e}


def _tool_result(payload: dict) -> dict:
    """MCP tools/call result: a text content block plus structuredContent."""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": False,
    }


def _tool_error(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": f"Error: {message}"}],
        "isError": True,
    }


def handle(message: dict) -> dict | None:
    """Dispatch one JSON-RPC message. Returns a response dict, or None for a
    notification (no id)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _err(None, INVALID_REQUEST, "not a valid JSON-RPC 2.0 message")

    method = message.get("method")
    id_ = message.get("id")
    is_notification = "id" not in message
    params = message.get("params") or {}

    # notifications (initialized, cancelled, ...) get no response
    if is_notification:
        return None

    try:
        if method == "initialize":
            return _ok(id_, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "Deterministic reasoning-pattern recommender. Call "
                    "diagnose_requirements first, ask the returned clarifying "
                    "questions, then recommend_patterns with the answers."
                ),
            })

        if method == "ping":
            return _ok(id_, {})

        if method == "tools/list":
            return _ok(id_, {"tools": [
                {"name": t.name, "description": t.description,
                 "inputSchema": t.input_schema}
                for t in tools.TOOLS
            ]})

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            tool = tools.BY_NAME.get(name)
            if tool is None:
                return _ok(id_, _tool_error(f"unknown tool '{name}'"))
            try:
                payload = tool.handler(arguments)
                return _ok(id_, _tool_result(payload))
            except ValueError as e:
                return _ok(id_, _tool_error(str(e)))
            except Exception as e:  # noqa: BLE001
                return _ok(id_, _tool_error(f"internal error: {e}"))

        return _err(id_, METHOD_NOT_FOUND, f"method not found: {method}")

    except Exception as e:  # noqa: BLE001
        return _err(id_, INTERNAL_ERROR, str(e))


def handle_raw(raw: str) -> str | None:
    """Parse, dispatch, serialise. Handles single messages and batches."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return json.dumps(_err(None, PARSE_ERROR, "parse error"))

    if isinstance(parsed, list):
        responses = [r for r in (handle(m) for m in parsed) if r is not None]
        return json.dumps(responses) if responses else None

    response = handle(parsed)
    return json.dumps(response) if response is not None else None
