"""patcomp_mcp — the pattern compiler as a Model Context Protocol server.

The reasoning core (patcomp) is deterministic: no model, no network. This
package exposes it over MCP so an agent runtime — Copilot Studio, Foundry, or
any MCP client — can call it as a set of tools while the agent's own LLM runs
the conversation with the architect.
"""
__version__ = "0.1.0"
