"""MCP transports: stdio (local / Claude Desktop) and Streamable HTTP
(Copilot Studio, Foundry, any HTTP MCP client).

Both feed decoded messages to server.handle and write back its output. No third-
party web framework: stdlib http.server keeps the container tiny and portable.
"""
from __future__ import annotations

import json
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import server

# Reject oversized request bodies before reading them into memory. A pattern
# recommendation payload is a few KB; 4 MiB is a generous ceiling that still
# closes the memory-exhaustion vector.
MAX_BODY_BYTES = 4 * 1024 * 1024


# ------------------------------------------------------------------ stdio
def serve_stdio() -> None:
    """Newline-delimited JSON-RPC over stdin/stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        out = server.handle_raw(line)
        if out is not None:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()


# ------------------------------------------------------------------ http
class _Handler(BaseHTTPRequestHandler):
    server_version = "patcomp-mcp/0.1"
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: bytes, ctype: str = "application/json",
              extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *_args):  # quiet by default
        pass

    def do_GET(self):
        # health check + a friendly note; the MCP stream itself is POST-driven
        if self.path.rstrip("/") in ("", "/health", "/healthz"):
            self._send(200, b'{"status":"ok","server":"patcomp-mcp"}')
        else:
            # Streamable HTTP GET (server-initiated stream) is not used by this
            # request/response server; 405 is a valid response per the spec.
            self._send(405, b'{"error":"method not allowed; POST JSON-RPC to /mcp"}')

    def do_POST(self):
        if self.path.rstrip("/") not in ("/mcp", ""):
            self._send(404, b'{"error":"not found; POST to /mcp"}')
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            self._send(400, b'{"error":"invalid Content-Length"}')
            return
        if length > MAX_BODY_BYTES:
            self._send(413, b'{"error":"request body too large"}')
            return
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._send(400, json.dumps(
                server._err(None, server.PARSE_ERROR, "parse error")).encode())
            return

        is_initialize = (isinstance(parsed, dict)
                         and parsed.get("method") == "initialize")
        out = server.handle_raw(raw)

        extra = {}
        session = self.headers.get("Mcp-Session-Id")
        if is_initialize and not session:
            extra["Mcp-Session-Id"] = uuid.uuid4().hex
        elif session:
            extra["Mcp-Session-Id"] = session

        if out is None:
            # a notification: 202 Accepted, no body
            self._send(202, b"", extra=extra)
        else:
            self._send(200, out.encode("utf-8"), extra=extra)


def serve_http(host: str = "0.0.0.0", port: int = 8080) -> None:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    print(f"patcomp-mcp listening on http://{host}:{port}/mcp", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
