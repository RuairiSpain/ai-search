"""Real MCP server for the workshop (streamable HTTP), deployed to Container Apps.

One server, tool groups per pattern. The DATA is synthetic (Contoso), the MCP
protocol and transport are real: hosted agents attach via the `mcp` tool type
and call these over the network — exactly the production shape.

Injection exercise: ticket TCK-9007's body contains a prompt-injection payload.
Pattern 02's failure-case eval checks the agent treats it as untrusted data (§6
"observation hygiene"), not as instructions.
"""
from __future__ import annotations

import contextlib
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

from routes import crm, graph, identity, migration, rules, telemetry_data

mcp = FastMCP("contoso-enterprise", stateless_http=True)
crm.register(mcp)
graph.register(mcp)
identity.register(mcp)
migration.register(mcp)
rules.register(mcp)
telemetry_data.register(mcp)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield

app = FastAPI(lifespan=lifespan)


# /health MUST be registered before the "/" mount: Starlette matches routes in
# registration order, and a mount at "/" would otherwise swallow every path.
@app.get("/health")
def health():
    return {"status": "ok"}


# Optional shared-key auth. The workshop default is OPEN (synthetic data, short-
# lived infra) — but say so out loud with customers: a real deployment gates this
# behind APIM/Easy Auth. Set MCP_API_KEY on the Container App and add the same
# value as an "x-api-key" header in the agent tool definition to enable.
_API_KEY = os.environ.get("MCP_API_KEY", "")


@app.middleware("http")
async def _auth(request: Request, call_next):
    if _API_KEY and request.url.path != "/health":
        if request.headers.get("x-api-key") != _API_KEY:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


app.mount("/", mcp.streamable_http_app())
