"""Entry point.

    python -m patcomp_mcp --http [--port 8080]     # Copilot Studio / Foundry
    python -m patcomp_mcp --stdio                   # local / Claude Desktop
"""
from __future__ import annotations

import argparse
import os

from . import tools, transport


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="patcomp-mcp",
                                 description="Pattern compiler MCP server.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--http", action="store_true", help="serve Streamable HTTP")
    mode.add_argument("--stdio", action="store_true", help="serve stdio")
    ap.add_argument("--host", default=os.environ.get("MCP_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    ap.add_argument("--catalogue", default=os.environ.get("PATCOMP_CATALOGUE"))
    args = ap.parse_args(argv)

    if args.catalogue:
        tools.set_catalogue(args.catalogue)

    if args.stdio:
        transport.serve_stdio()
    else:
        transport.serve_http(args.host, args.port)  # default to HTTP
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
