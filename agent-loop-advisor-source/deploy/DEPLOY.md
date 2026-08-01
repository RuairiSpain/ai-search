# Deploying the Pattern Compiler MCP server

The server is a single stdlib-only Python process (plus PyYAML). It speaks MCP
Streamable HTTP on `/mcp` and has a health check on `/health`. No model call,
no outbound network, no database — so it scales to zero cleanly and starts fast.

## Run locally

```bash
pip install -e .
patcomp-mcp --http --port 8080          # Streamable HTTP (Copilot Studio / Foundry)
patcomp-mcp --stdio                      # stdio (Claude Desktop, local MCP clients)
# health:  GET  http://localhost:8080/health
# mcp:     POST http://localhost:8080/mcp   (JSON-RPC 2.0)
```

## Build and run the container

```bash
docker build -t patcomp-mcp:0.1 .
docker run -p 8080:8080 patcomp-mcp:0.1
```

## Option A — Azure Container Apps (recommended for Copilot Studio)

Container Apps gives you HTTPS, scale-to-zero and a stable ingress URL, which is
exactly what the Copilot Studio connector needs.

```bash
az containerapp env create -g <rg> -n patcomp-env -l <region>

az containerapp create \
  -g <rg> -n patcomp-mcp \
  --environment patcomp-env \
  --image <your-registry>/patcomp-mcp:0.1 \
  --target-port 8080 \
  --ingress external \
  --min-replicas 0 --max-replicas 3 \
  --cpu 0.25 --memory 0.5Gi

# the ingress FQDN it prints is your connector host:
#   https://patcomp-mcp.<hash>.<region>.azurecontainerapps.io/mcp
```

Put that FQDN into `agent/mcp-connector.swagger.yaml` (`host:`) and into the
M365 manifest `validDomains`.

## Option B — Azure AI Foundry

Two ways to host it in Foundry:

1. **As a custom MCP tool for a Foundry Agent.** Foundry Agent Service can call
   an MCP server over HTTP. Deploy the container (Container Apps or a Foundry
   managed online endpoint), then register the `/mcp` URL as an MCP tool on the
   Foundry agent. The agent's model handles the conversation; this server
   handles the deterministic logic — the same split Copilot Studio uses.

2. **As a managed online endpoint.** Push the image to your Foundry/AML
   workspace registry and create an online endpoint from it, exposing port
   8080. Use the endpoint URL as the MCP host.

Either way the contract is identical: POST JSON-RPC to `/mcp`, MCP Streamable
HTTP, five tools discovered via `tools/list`.

## Wire it into Copilot Studio

1. Deploy the server (Option A or B) and note the HTTPS host.
2. Edit `agent/mcp-connector.swagger.yaml` — set `host:` to your FQDN.
3. In Copilot Studio: **Tools → Add a tool → New tool → Custom connector**,
   then **Import an OpenAPI file** and select the edited swagger. The
   `x-ms-agentic-protocol: mcp-streamable-1.0` extension registers it as an MCP
   server; the five tools appear automatically.
4. Add the connector to your agent, and paste `agent/instructions.md` into the
   agent's instructions (or publish the whole `agent/` folder as a declarative
   agent via the M365 manifest).
5. Test in the Copilot Studio test pane: *"My retrieval works but the
   recommendations are inconsistent — what pattern do I need?"*

## Publish the agent to Microsoft 365

The `agent/` folder is a declarative-agent app package:

- `declarative-agent.json` — the agent (name, instructions, the MCP action).
- `m365-manifest.json` — the M365/Teams app manifest that carries it.
- `mcp-connector.swagger.yaml` — the MCP connector.

Steps:

1. Fill the placeholders: a GUID for `id`, your host in `validDomains`, and add
   `color.png` (192×192) and `outline.png` (32×32) icons.
2. Zip `m365-manifest.json`, the two icons, `declarative-agent.json`,
   `instructions.md` and `mcp-connector.swagger.yaml` into an app package.
3. Upload in the Microsoft 365 Admin Center (or via Teams Developer Portal /
   `m365 app` tooling) and publish to your org.
4. It appears in the Microsoft 365 Copilot agents list for assigned users.

## Security and operations

- **Auth.** The server itself is unauthenticated by default — put it behind the
  platform's ingress auth (Container Apps auth, Foundry endpoint keys, or an
  API Management gateway with a key the Copilot Studio connector sends). Do not
  expose `/mcp` publicly without a gate; it is compute you are paying for.
- **The requirements text is customer input.** It is treated as data, never
  instruction — the intake stage flags injection markers and never lets scenario
  text become a directive. Still, set a retention and region policy on any
  logging you add.
- **Statelessness.** Every tool call is self-contained; there is no session
  state to lose. `Mcp-Session-Id` is issued on initialize and accepted but not
  required, so horizontal scaling needs no sticky sessions.
- **Observability.** Add request logging at the ingress. The recommender's own
  kill-log and tier-log (see `patcomp`) are the useful telemetry to forward if
  you wire structured logging.
