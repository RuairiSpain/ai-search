# Tier 2 — Hosted Agents

⚠ marks items requiring an empirical check before build — tracked centrally
in `08-open-items-and-experiments.md`.

## 1. What tier 2 is

Your code, containerised, run by Foundry in a per-session VM-isolated
sandbox with a persistent `$HOME`. You own orchestration; the platform owns
compute, identity, session state, scaling and observability.

Sizing: the concept doc lists 0.5 vCPU/1 GiB, 1/2, and 2/4. ⚠ The
`azure.yaml` reference lists 0.25–4.0 vCPU and 0.5–8.0 GiB. Both are
current at time of writing — test what your region accepts.

Disk: up to 20 GiB at 1 vCPU or larger, scaling down proportionally below,
~20% reserved for system, and the remainder is **shared between your
container image, `$HOME`, and every other writable path**. A fat image eats
the user's workspace.

Billing is CPU + memory across all *active sessions*, so cost scales with
concurrent users, not requests. This inverts T1's economics and is the main
reason not to put a high-user-count, low-intensity chat app here.

## 2. Deployment

### 2.1 Project structure

```
triage-agent/
├── azure.yaml                    # single source of truth (replaces the old
│                                 # agent.manifest.yaml + agent.yaml pair)
├── infra/                        # only if you eject IaC
├── src/
│   └── triage/
│       ├── main.py               # protocol host — entry point
│       ├── agents.py             # MAF agent definitions
│       ├── workflow.py           # orchestration graph
│       ├── toolbox.py            # MCP client for the Toolbox endpoint
│       ├── progress.py           # progress events -> gateway (see §5.4)
│       ├── requirements.txt
│       └── Dockerfile            # only for --deploy-mode container
├── tests/
│   ├── test_local.py
│   └── test_deployed_isolation.py   # MUST run against a deployed agent
└── Makefile
```

`main.py` is the only file the platform contract touches. Everything else
is ordinary application code.

### 2.2 CLI

```bash
# one-time
azd ext install microsoft.foundry
azd auth login

# scaffold from a sample manifest
azd ai agent init -m "<agent.manifest.yaml url>"
azd env set AZURE_SUBSCRIPTION_ID <sub-id>
azd env set AZURE_LOCATION <region>      # must be a hosted-agent region

# provision Foundry project, model deployments, ACR, App Insights
azd provision

# local run — NOTE: no platform user context, isolation is NOT testable here
azd ai agent run --no-inspector
azd ai agent invoke --local "Triage ticket 4412"

# deploy
azd deploy
azd ai agent show                    # confirm Active
azd ai agent invoke "Triage ticket 4412"
azd ai agent monitor --follow        # stream container logs

# sessions
azd ai agent sessions list

# teardown
azd down
```

Deploy modes: `code` uploads source as a ZIP and builds remotely (default
for Python and .NET); `container` builds from your Dockerfile. Choose at
init with `--deploy-mode`. Bicep-less by default;
`azd ai agent init --infra=bicep|terraform` ejects IaC when needed.

### 2.3 `azure.yaml`

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Azure/azure-dev/main/schemas/v1.0/azure.yaml.json
name: triage-agent-project

requiredVersions:
  extensions:
    azure.ai.agents: '>=0.1.0-preview'

services:
  ai-project:
    host: azure.ai.project
    deployments:
      - name: gpt-5.4-mini
        model: { format: OpenAI, name: gpt-5.4-mini, version: "2026-03-17" }
        sku:   { name: GlobalStandard, capacity: 50 }

  # --- connections: one per downstream system, each with its own auth ---

  fabric-iq-conn:
    host: azure.ai.connection
    uses: [ai-project]
    category: RemoteTool
    target: ${FABRIC_IQ_MCP_ENDPOINT}
    authType: UserEntraToken        # see §4
    audience: ${FABRIC_IQ_AUDIENCE}

  search-conn:
    host: azure.ai.connection
    uses: [ai-project]
    category: CognitiveSearch
    target: https://my-search.search.windows.net
    authType: AAD                   # agent identity — shared, not per-user

  # --- toolbox: hosted agents CANNOT declare tools inline ---

  triage-tools:
    host: azure.ai.toolbox
    uses: [ai-project, fabric-iq-conn, search-conn]
    description: Tools for ticket triage.
    tools:
      - type: mcp
        connection: fabric-iq-conn
      - type: azure_ai_search
        connection: search-conn
      - type: code_interpreter

  # --- the agent ---

  triage-agent:
    host: azure.ai.agent
    kind: hosted
    name: triage-agent
    project: src/triage
    language: docker
    uses: [ai-project, triage-tools]
    toolboxes: [triage-tools]
    startupCommand: python main.py
    protocols:
      - protocol: responses
        version: 2.0.0              # 1.0.0 is BLOCKED after 31 Jul 2026
    env:
      FOUNDRY_MODEL_NAME: ${FOUNDRY_MODEL_NAME}
      TOOLBOX_NAME: triage-tools
      LOG_LEVEL: info
    container:
      resources:
        cpu: "1.0"
        memory: 2Gi
```

**Do not declare `FOUNDRY_PROJECT_ENDPOINT` in `env`.** The platform
injects it and `azd ai agent run` sets it locally; declaring it shadows the
platform value.

Environment variables are **immutable per version**. Changing one requires
a new version, which means a full cutover (§6.2).

Platform-injected env vars include `FOUNDRY_PROJECT_ENDPOINT`,
`FOUNDRY_AGENT_VERSION`, and `FOUNDRY_AGENT_SESSION_ID`.

### 2.4 Protocol choice — mandate `responses`

Make this a linter rule, not a preference. Three reasons:

1. Platform-managed conversation history, which `gw_context` assumes.
2. Built-in `background: true` with platform-managed polling and
   cancellation.
3. **OAuth identity passthrough for MCP tools is only supported when
   invoking agents via the Responses protocol.** Choosing `invocations`
   forfeits it.

`invocations` is correct only for webhook receivers, batch/classification
work, and custom streaming protocols — none of which have an end user to
isolate.

## 3. Identity delegation — reference implementation

See `00-tier-model-and-concepts.md` §5 for the end-to-end diagram and the
delegation-vs-passthrough table. This section is the working code.

### 3.1 What this does and does not do

```
Chat UI                Gateway                      Foundry
────────               ───────                      ───────
MSAL token   ──POST──> validate signature
for GATEWAY             extract oid + tid
scope                   principal.subject
                              │
                              ├─ auth: gateway managed identity ──┐
                              └─ header: x-ms-user-identity ──────┤
                                                                  ▼
                                                    session + $HOME scoped
                                                    to that opaque string;
                                                    container still runs as
                                                    the AGENT identity
```

| Scoped per user | Still the agent identity |
| --- | --- |
| `agent_session_id` | model inference |
| `$HOME`, `/files` | Toolbox tool calls* |
| conversation history | downstream Azure services |

\* unless that connection uses `UserEntraToken` or `OAuth2` — a separate
dial, configured per connection, requiring the user to hold **Foundry
Agent Consumer** on the project and to be in the **same tenant** as the
Foundry project.

The user's token is never forwarded to Foundry. Only the derived opaque
identifier is.

### 3.2 Config

```yaml
# apps.yaml — auth block, shared across every configured app (T2/T3)
auth:
  tenant_id: ${GATEWAY_TENANT_ID}
  audience: api://a2a-gateway     # the GATEWAY's own app registration
  subject_claim: oid              # NEVER email/upn — mutable and recycled

apps:
  - name: ticket-triage
    tier: t2
    upstream: triage-hosted
    default_mode: long

upstreams:
  - id: triage-hosted
    tier: t2
    project_endpoint: ${FOUNDRY_PROJECT_ENDPOINT}
    agent_name: triage-agent
    identity: per_user            # per_user (default) | service
```

Chat UI acquires a token for `api://a2a-gateway/.default` — **not** for
`https://ai.azure.com`. The UI has no Foundry relationship at all.

### 3.3 Principal extraction

```python
# app/auth/principal.py
from __future__ import annotations

import re
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

# Charset accepted by x-ms-user-identity: 1-256 chars, letters, digits,
# and . _ : - @   Reject rather than sanitise — a mangled identifier that
# normalises onto another user's value is a silent cross-user data leak.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,256}$")


class AuthError(Exception):
    """Always surfaces as 401. Never leak which check failed."""


@dataclass(frozen=True)
class Principal:
    subject: str        # "{tid}.{oid}" — globally unique, immutable
    tenant: str

    def user_identity_header(self) -> str:
        if not _USER_ID_RE.fullmatch(self.subject):
            raise AuthError("principal is not a valid x-ms-user-identity")
        return self.subject


class EntraValidator:
    """Validates the inbound bearer token from the chat UI.

    This is the ONLY place a user identity enters the system. Nothing else
    may read a user id from a request body, query string or custom header.
    """

    def __init__(self, tenant_id: str, audience: str, subject_claim: str = "oid"):
        self._jwks = PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
            cache_keys=True,
        )
        self._issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self._audience = audience
        self._subject_claim = subject_claim

    def principal_from(self, authorization: str | None) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthError("missing bearer token")
        token = authorization[7:]

        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],          # never accept "none" or HS*
                audience=self._audience,       # OUR audience, not Foundry's
                issuer=self._issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError("token validation failed") from exc

        oid = claims.get(self._subject_claim)
        tid = claims.get("tid")
        if not oid or not tid:
            raise AuthError("token missing required claims")

        # oid is unique within a tenant, not globally. Qualify it, or two
        # users in different tenants can collide onto one sandbox.
        return Principal(subject=f"{tid}.{oid}", tenant=tid)
```

### The anti-pattern this replaces

```python
# NEVER. Anything the client sends, the client controls.
user_id = request.headers.get("x-user-id")          # ✗
user_id = (await request.json())["user"]["id"]      # ✗
user_id = claims["email"]                           # ✗ mutable + recycled
```

### 3.4 Adapter

The full `FoundryHostedAdapter` implementation, shared with the adapter
contract, is in `01-gateway-config-and-adapter-contract.md` §2. Key point
repeated here because it's the most common mistake in this design: the
adapter **authenticates to Foundry as the gateway**. Delegating user
scoping via the opaque `x-ms-user-identity` header is a completely
separate, additive mechanism — conflating the two is how isolation bugs
happen.

### 3.5 Wiring the A2A surface

```python
# app/api/a2a.py
@router.post("/apps/{app}/")
async def a2a_message_send(
    app: str,
    body: JsonRpcRequest,
    authorization: str | None = Header(default=None),
):
    principal = validator.principal_from(authorization)   # 401 on failure

    context_id = body.params.message.context_id
    if context_id:
        # THE control. A client-supplied contextId that isn't authorised
        # against this principal is a direct IDOR. Deny, don't repair.
        ctx = await store.authorise_context(context_id, principal)
        if ctx is None:
            raise HTTPException(404)        # 404, not 403 — don't confirm it exists
    else:
        ctx = await store.new_context(app, principal)

    adapter = registry.for_app(app)
    submission = await adapter.submit(
        principal=principal,
        ref=ctx.upstream_ref,
        text=_text_of(body.params.message),
        blocking=body.params.configuration.blocking,
    )
    await store.record(ctx, submission)
    return _as_a2a_task(submission)
```

`authorise_context` returns the row only when `principal_subject` matches.
It is not a lookup followed by a check — the principal is part of the
query, so there is no path that reads the row without it.

### 3.6 Verification

Run against a **deployed** agent. Local runs don't receive platform user
context, so this entire class of bug is invisible in dev.

```python
async def test_sessions_are_isolated():
    alice = Principal(subject="t1.alice", tenant="t1")
    bob   = Principal(subject="t1.bob",   tenant="t1")

    a = await adapter.submit(principal=alice, ref=UpstreamRef(),
                             text="Write 'alice' to ~/marker.txt", blocking=True)
    b = await adapter.submit(principal=bob, ref=UpstreamRef(),
                             text="Read ~/marker.txt", blocking=True)

    assert a.ref.session_id != b.ref.session_id      # distinct sandboxes
    assert "alice" not in b.inline_result            # no cross-read

async def test_context_is_not_transferable():
    ctx = await store.new_context("ticket-triage", alice)
    assert await store.authorise_context(ctx.context_id, bob) is None
```

The second test is the one that matters. The platform does not fence
delegated users from each other — it separates delegated from
non-delegated callers only. `authorise_context` is the boundary.

### Checklist

- [ ] Gateway app registration exists; UI requests `api://a2a-gateway/.default`
- [ ] Gateway identity holds `.../UserIdentityImpersonation/action` on the agent
- [ ] Agent runs container protocol **2.0.0** (1.0.0 blocked after 31 Jul 2026)
- [ ] `subject_claim: oid`, qualified with `tid`; never email or upn
- [ ] No code path reads a user identifier from a request body or custom header
- [ ] Both tests above run in CI against a deployed agent
- [ ] Startup probe fails readiness when delegation is unavailable

## 4. Fabric IQ with user identity

Fabric IQ isn't a preference, it's a requirement for per-user data access
on T2: the integration uses identity passthrough (On-Behalf-Of) and does
not support service principal authentication **at all**. Agent identity is
not a weaker option here — it simply fails.

### 4.1 The YAML property

```yaml
  fabric-iq-conn:
    host: azure.ai.connection
    uses: [ai-project]
    category: RemoteTool
    target: ${FABRIC_IQ_MCP_ENDPOINT}
    authType: UserEntraToken        # "Entra passthrough" / managed user
                                    # identity passthrough
    audience: ${FABRIC_IQ_AUDIENCE}
```

Supported MCP connection auth types are `CustomKeys`, `OAuth2` (managed or
custom), `AgenticIdentityToken`, and `UserEntraToken`. Only the last two
non-key options carry user context.

**The `audience` is the resource identifier of the downstream service, not
the MCP server's URL.** An incorrect audience fails authentication even
when RBAC is correct. For Microsoft 365 MCP servers the audience is the
Agent 365 first-party app GUID (`ea9ffc3e-8a23-4a7d-836d-234d7c7565c1`),
not the server URL. Confirm the correct audience for Fabric IQ against its
own documentation rather than guessing from the pattern.

### 4.2 Why Fabric requires it

Fabric's integration runs queries using the signed-in user's identity;
every end user needs access to the data agent and its underlying sources
or the call fails.

Requirements to satisfy before this can function:

- Foundry User role on the project for the developer identity, **the
  agent's runtime identity**, and every user identity in the OAuth flow
- Foundry Project Manager to create the connection
- Fabric IQ licence for every user who invokes it through your agent
- Same tenant — cross-tenant token exchange is unsupported
- `offline_access` in scopes so tokens auto-refresh

### 4.3 ⚠ The unresolved part — highest-risk open item in tier 2

Passthrough must be **mediated by Toolbox**. If your container acquires a
token itself — `DefaultAzureCredential` inside the sandbox resolves to the
agent's managed identity — the MCP server sees the agent, never the user.
That's documented behaviour for agent identity auth: fine for
service-to-service, wrong for per-user authorisation.

Prompt agents demonstrably support the consent flow: the response carries
an `oauth_consent_request` with a `consent_link`, the user signs in,
subsequent calls carry their token. For **hosted agents** there is a
documented developer report of being unable to find documentation or a
sample showing whether a container can consume an OAuth identity
passthrough connection or trigger that consent flow, and of hitting
`AADSTS50013` because the token Foundry passes into the container has
audience `https://ai.azure.com`, which doesn't match a custom MCP server's
app registration.

**Experiment T2-FAB-1** — run before designing anything around Fabric RLS:

1. Deploy a trivial hosted agent with one `UserEntraToken` Toolbox
   connection.
2. Invoke through the gateway as a user with Fabric access.
3. Confirm a consent request surfaces to the gateway, and that the gateway
   can relay `consent_link` to the chat UI and resume afterwards.
4. Confirm Fabric returns *that user's* rows, and that a second user with
   narrower permissions sees fewer.

**T2-FAB-1 can invert the escalation table.** If it fails, per-user Fabric
access is a **T1-only** capability, and data-sensitive apps stay on prompt
agents while T2 becomes the tier for compute-heavy work over *shared*
data. Know this before the sample library is written — it is the single
open item most likely to change the design.

Consent also needs a UI surface: a link to render, and a way to resume the
conversation after the user completes it. That's front-end work, not
config.

## 5. Multi-agent patterns inside T2

This is where T2 earns its cost. Orchestration lives in *your* container,
so you get MAF's full pattern set — Sequential, Concurrent, Handoff, Group
Chat, Magentic — with no workflow YAML and no per-node platform round
trip.

### 5.1 Protocol host

⚠ **Corrected:** an earlier draft of this snippet imported
`ResponsesHostServer` from `agent_framework.foundry.hosting`. That class
does not exist — confirmed by downloading and inspecting the real,
installed `agent-framework-foundry` package (1.10.3): it has no `hosting`
submodule at all, only client/agent-authoring surface
(`FoundryChatClient`, `FoundryAgent`, evals). The real hosting server lives
in a different, separate package — `azure-ai-agentserver-responses`,
`azure.ai.agentserver.responses.hosting`, exporting
`ResponsesAgentServerHost` — found while fixing the gateway's own T2
progress-narration gap (`docs/08-open-items-and-experiments.md` item 16,
`samples/tier2/04-long-running-hello-world`). Same class of mistake as the
`A2AFastAPIApplication` correction in `06-tier3-durable-agents.md` §4.1:
verify against the installed package before writing real code, every time
— this surface moves fast and source drafts go stale.

```python
# src/triage/main.py
import os

from agent_framework.foundry import FoundryChatClient
from azure.ai.agentserver.responses.hosting import ResponsesAgentServerHost
from azure.identity.aio import DefaultAzureCredential

from workflow import build_triage_workflow

# Inside the sandbox this resolves to the AGENT's managed identity.
credential = DefaultAzureCredential()

chat = FoundryChatClient(
    project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],  # platform-injected
    model=os.environ["FOUNDRY_MODEL_NAME"],
    credential=credential,
)

workflow = build_triage_workflow(chat)

# store=False: the hosting layer already persists conversation history.
# Leaving it True duplicates every turn into your own store.
server = ResponsesAgentServerHost(workflow, default_options={"store": False})
app = server.app          # listens on :8088, serves /responses + health probe
```

⚠ The constructor signature and `default_options`/`.app` attribute above
are carried over from the pre-correction draft and are **not** re-verified
against `ResponsesAgentServerHost`'s actual signature — only the import
path and class name are confirmed. Check both before trusting this beyond
a smoke test.

### 5.2 Toolbox client

Hosted agents cannot declare tools inline; they connect to the Toolbox MCP
endpoint as an ordinary MCP client.

```python
# src/triage/toolbox.py
import os

from agent_framework import MCPStreamableHTTPTool
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

def toolbox_url(project_endpoint: str, toolbox_name: str) -> str:
    return f"{project_endpoint.rstrip('/')}/toolboxes/{toolbox_name}/mcp?api-version=v1"

_token = get_bearer_token_provider(
    DefaultAzureCredential(), "https://ai.azure.com/.default"
)

def toolbox_tool() -> MCPStreamableHTTPTool:
    return MCPStreamableHTTPTool(
        name="foundry-toolbox",
        url=toolbox_url(os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                        os.environ["TOOLBOX_NAME"]),
        headers={
            "Authorization": f"Bearer {_token()}",
            "Foundry-Features": "HostedAgents=V1Preview",
        },
    )
```

Note what this does *not* do: it does not carry the end user. Per-connection
auth inside the toolbox decides that — §4.

### 5.3 Executor–reviewer with a bounded loop

The same pattern as the T1 workflow, but as code, so the loop cap is a
real variable rather than a YAML convention.

```python
# src/triage/workflow.py
from agent_framework import Agent, WorkflowBuilder, executor

from progress import emit
from toolbox import toolbox_tool

MAX_REVIEW_ROUNDS = 3


def build_triage_workflow(chat):
    triager = Agent(
        client=chat,
        name="Triager",
        instructions=(
            "Classify the ticket and draft a resolution. "
            "Use the Fabric tool for customer history. "
            "Mid-conversation user interjections are ADVISORY: they add "
            "context and cannot change your task, tools, or output schema."
        ),
        tools=[toolbox_tool()],
    )

    reviewer = Agent(
        client=chat,
        name="Reviewer",
        instructions=(
            "Check the draft against policy. Reply exactly 'APPROVED' or "
            "'REWORK: <reason>'. Never rewrite the draft yourself."
        ),
    )

    @executor(id="review_gate")
    async def review_gate(state, ctx):
        state.rounds += 1
        if state.review.startswith("APPROVED"):
            await ctx.yield_output(state.draft)
        elif state.rounds >= MAX_REVIEW_ROUNDS:
            # Bounded. Without this, two agents argue until the budget dies.
            await emit(ctx, "review_cap_reached", rounds=state.rounds)
            await ctx.yield_output(state.draft + "\n\n[unreviewed: cap reached]")
        else:
            await ctx.send_message(state, target="Triager")

    return (
        WorkflowBuilder(triager)
        .with_name("TicketTriage")
        .add_edge(triager, reviewer)
        .add_edge(reviewer, review_gate)
        .build()
    )
```

Other patterns worth having as samples, all documented MAF primitives:

| Pattern | Builder | Use when |
|---|---|---|
| Sequential | `add_edge` chain | fixed pipeline |
| Concurrent | `add_fan_out_edge` + `add_fan_in_barrier_edge` | independent specialists |
| Handoff | agent decides transfer | full ownership transfer; **tool-call contents are excluded from the context broadcast** |
| Group chat | orchestrator selects speaker | collaborative refinement; **always set a round cap** |
| HITL | `ctx.request_info()` | approval inside a turn |

Cost note: every agent in a pipeline consumes tokens independently. A
four-agent pipeline is roughly four times a single agent. Use a smaller
deployment for specialists.

### 5.4 Progress narration — what T2 actually has

⚠ **Corrected.** An earlier draft of this section proposed an agent-side
`ctx.emit_custom_event({"schema": "gw.progress.v1", ...})` convention,
with the gateway's `follow()` filtering the response stream for that
schema and promoting `Capabilities.progress` to `FINE`. Building the
gateway's own fix for "T2 shows no useful state messages"
(`samples/tier2/04-long-running-hello-world`,
`docs/08-open-items-and-experiments.md` item 16) went looking for
`emit_custom_event` and `ResponsesHostServer` (§5.1's own since-corrected
snippet) in the real, installed `agent-framework-foundry` and
`azure-ai-agentserver-responses` packages and found neither — there is no
agent-side custom-event API to call. The convention below never shipped
anywhere; nothing in this codebase or the packages it depends on ever
implemented it.

**What actually exists, and what the gateway now does with it:**
`Response.output` — a real, standard field on every polled Response,
confirmed against the installed `openai` package's `ResponseOutputItem`
union (the `_openai` client `AIProjectClient.get_openai_client()` returns
is a genuine `openai.AsyncOpenAI`, not a Foundry-specific type) — is an
ordered list of items the platform attaches to the response as the agent
works: `function_call`, `mcp_call`, `code_interpreter_call`,
`web_search_call`, `reasoning`, `message`, and others, each with its own
`type` and (for calls) a `name`/`status`. `FoundryResponsesAdapter.follow()`
(`src/gateway/upstream/foundry_responses.py`) reads the most recent item on
every poll and derives a short narration line from it — "running tool:
fabric_query", "running code interpreter", "thinking" — with **no
agent-side code required at all**. Every T2 agent gets this automatically,
for free, purely from tool-call/reasoning items the platform already
produces.

This is deliberately still declared `COARSE`, not `FINE`
(`FoundryResponsesAdapter.capabilities`): it's best-effort and
coarse-grained by nature — one line per *item* (which tool is running), not
per *step within* a tool call, and an agent that calls no tools at all (or
whose poll lands before the first item appears) gets no narration line.
T3's `gw.progress.v1` webhook push (tier3 doc §5.4) is still the real thing
that convention originally promised: orchestrator-authored, step-level
narration the agent author chooses explicitly. T2's mechanism and T3's are
not the same event vocabulary — they arrive at the gateway through
different code paths (T2: derived every poll from `resp.output`; T3:
explicit webhook payload) — but both end up as the same
`StatusEvent.detail: str | None`, and both now actually reach the A2A wire
via `TaskUpdater.update_status(state, message=...)`
(`src/gateway/a2a_server/executor.py`) — that pass-through was also missing
until the same fix, for every tier, not just T2 — see docs/08 item 15.

### 5.5 Session filesystem

```python
from pathlib import Path

HOME = Path.home()          # persists across turns and idle periods

def working_dir(conversation_id: str) -> Path:
    # Version-stamp anything on disk. There is no traffic splitting, so a
    # deploy switches every live session at once and new code will meet
    # $HOME written by old code.
    d = HOME / "work" / conversation_id
    d.mkdir(parents=True, exist_ok=True)
    (d / ".schema").write_text("v2")
    return d
```

Files written under `$HOME` survive idle. They do **not** survive session
deletion at 30 days, and they share the disk budget with your container
image. Harvest anything durable to the gateway's blob store
(`07-artifacts-and-code-interpreter.md`).

## 6. The three planes

### 6.1 Architecture

| Item | State |
|---|---|
| Gateway is the traffic splitter | ◆ open |
| T2 progress fidelity → `FINE` | ✓ decided — §5.4 above |
| Downstream per-user identity | ⚠ T2-FAB-1 (§4.3) |
| Region topology | ◆ open |

**Traffic splitting.** An agent endpoint serves one version at a time and
traffic splitting is unsupported. If you want canary, the gateway does it:
deploy `triage-agent-v2` as a **separate agent name** and split at
routing. `gw_context` must then pin the *agent name*, not just the
version, or a user mid-conversation flips cohorts between turns.

**Region topology.** Hosted agents are region-limited, the sandbox runs in
the project's region, and blob storage plus gateway should match for
latency and residency. This constrains where an app can be offered; put it
on the agent card.

### 6.2 Control plane

| Item | State |
|---|---|
| Registry drift detection | ◆ open |
| RBAC provisioning automation | ◆ open |
| Version cutover runbook | ◆ open |
| Model quota contention | ⚠ verify |
| Cost attribution export | ◆ open |

**Registry drift.** Nothing detects that a named Foundry agent was
deleted, renamed, or redeployed with different protocols. Reconcile in the
linter per environment *and* at gateway startup, or the first symptom is a
user-facing 404.

**RBAC provisioning.** `UserIdentityImpersonation` is granted per agent.
Onboarding a T2 app is currently a manual Azure step that silently
degrades isolation if skipped — the readiness probe catches it, but only
post-deploy. Automate it in the pipeline that creates the agent.

**Version cutover runbook.** Given no traffic splitting and immutable
per-version env vars:

1. Announce a drain window, or accept mid-session cutover.
2. Confirm `$HOME` schema stamps let new code detect old-format state.
3. Deploy; watch `gw_task` failure rate for the first N minutes.
4. Rollback = redeploy the previous version. Retaining old versions (D8)
   is what makes this possible.

**Quota contention.** ⚠ Agents share the project's model deployments and
TPM. A busy T2 agent can throttle an unrelated T1 app. Verify whether
quota is per-deployment and whether noisy apps need their own.

**Cost attribution.** Billing is CPU + memory across active sessions.
`gw_context` already maps `session_id → principal_subject`, so per-user
and per-app attribution is available — but only if you export it.
Retrofitting is painful; do it at first deploy.

### 6.3 Data plane

| Item | State |
|---|---|
| Trace correlation | ✓ gateway-side built; ⚠ Foundry's own handling unverified |
| Concurrent turns in one session | ⚠ verify; serialise regardless |
| Session creation race | ✓ decided — advisory lock |
| Session/conversation divergence | ◆ open |
| Cold start vs timeouts | ◆ measure |
| Submit idempotency | ✓ decided — dedupe on messageId |
| Payload / upload limits | ⚠ unknown |

**Trace correlation.** The gateway's own half is built, not just designed:
`src/gateway/tracing.py` extracts the inbound `traceparent` (or mints a
fresh one) at `GatewayCallContextBuilder.build()` — the same single entry
point request principal validation already runs through — persists the
active trace-id per task (`gw_task.trace_id`), and attaches a
correctly-formed outbound `traceparent` header (same trace-id, fresh
span-id) to every `submit()`/`follow()`/`resume()` call
`FoundryResponsesAdapter`/`FoundryHostedAdapter` make. What's still
unverified — and can only be verified against a live endpoint, which this
repo doesn't have — is the other half: whether Foundry's hosted-agent
Responses API proxy actually reads that header and correlates it into its
own container span, or just ignores it. App Insights being injected and
the protocol libraries emitting OpenTelemetry by default (per Microsoft's
own docs) doesn't by itself confirm this specific header survives that
specific hop. Until verified, treat "one trace-id, gateway through
container" as designed-for, not confirmed. See
`docs/08-open-items-and-experiments.md`.

**Concurrent turns.** ⚠ Unknown whether the platform serialises requests
to one `agent_session_id`. Serialise per session in the gateway
regardless — one microVM, one filesystem, so concurrency buys nothing and
corruption is hard to diagnose. This also makes `identity: service` more
dangerous: a shared session under concurrent load is this race
permanently. Any `service`-mode agent must be documented as
disk-stateless.

**Session creation race.** ✓ Two concurrent first turns from one user (two
tabs, or a retry) create two sessions with two `$HOME`s.
`gw_context_session_owner` is unique on `(app, session_id)` — it prevents
two users sharing a session, the opposite direction. Fix: Postgres
advisory lock on `(app, principal_subject, context_id)` around first-turn
creation, plus `INSERT ... ON CONFLICT DO NOTHING`, plus terminating the
orphan session on the losing side rather than leaking it.

**Session/conversation divergence.** Two clocks: session dies at 30 days
inactivity; conversation retention is our own 30-day sliding rule (D5).
The failure is a user returning to perfect chat history with every
working file gone. Policy: detect session-gone on resume, mint a new
session, re-materialise from blob where possible, and tell the user
plainly where it isn't.

**Cold start vs timeouts.** After 15 minutes idle the sandbox is
deprovisioned and restored on next use. Gateway request timeouts and
reaper lease durations must both exceed worst-case restore. Measure it;
don't guess.

**Submit idempotency.** ✓ A timed-out submit that retries must not create
a second session and a second response. Dedupe on the A2A `messageId`
*before* the upstream call (`gw_inbound_message`).

**Payload limits.** ⚠ Unknown for `/responses` and `/files`. Matters for
the MCP-data-to-code-interpreter path where real datasets get uploaded.

## 7. Checklists

### Before first deploy

- [ ] Region supports hosted agents *and* the chosen model *and* the tools
- [ ] `protocols: responses, version: 2.0.0` (1.0.0 blocked after 31 Jul 2026)
- [ ] `azure-ai-agentserver-core >= 2.0.0b7`
- [ ] Gateway identity holds `UserIdentityImpersonation` on the agent
- [ ] No tools declared inline — Toolbox only
- [ ] `FOUNDRY_PROJECT_ENDPOINT` absent from `env`
- [ ] Container image sized against the shared disk budget

### Before trusting isolation

- [ ] Isolation tested against a **deployed** agent — local runs receive no
      platform user context and this bug class is invisible in dev
- [ ] Two principals → two `agent_session_id`s → two `$HOME`s
- [ ] `authorise_context` rejects a contextId presented by the wrong principal
- [ ] Startup probe fails readiness when delegation is unavailable

### Before promising per-user data

- [ ] T2-FAB-1 passed end to end (§4.3)
- [ ] Consent link relayed to the chat UI and conversation resumable after
- [ ] Every user has the Fabric IQ licence and source-level permissions
- [ ] All users in the same tenant as the Foundry project
