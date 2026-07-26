# Long-Running Agents — A2A Gateway on Microsoft Foundry

A single A2A ([Agent2Agent protocol](https://a2a-protocol.org/)) gateway that fronts
Microsoft Foundry agents of three different shapes — prompt agents, hosted
(containerised) agents, and Durable-Task orchestrations — behind one uniform
task/message contract, for one or more chat clients.

This folder is the merged, de-duplicated project plan assembled from thirteen
source documents (some in 2–3 revisions) written during design. Where later
revisions corrected or superseded earlier ones, this plan keeps only the
final position and notes the correction. See
[`docs/08-open-items-and-experiments.md`](docs/08-open-items-and-experiments.md)
for a full list of what changed between drafts.

## Why three tiers

| Tier | What runs | Owns orchestration | Identity model | Typical use |
|---|---|---|---|---|
| **T1 — Prompt agent** | Stateless model+tools call on Foundry's managed inference plane | Foundry (or a portal Workflow) | Conversation ID, gateway-owned | No-code agents, cheapest to run and to front |
| **T2 — Hosted agent** | Your container, in a per-session VM-isolated Foundry sandbox with persistent `$HOME` | Your code (Microsoft Agent Framework) | `x-ms-user-identity` delegation into a per-user sandbox | Custom logic, multi-agent orchestration, file output, sub-hour work |
| **T3 — Durable agent** | Azure Durable Functions / Durable Task, MAF + `a2a-sdk` | Your code, deterministic orchestrators | No platform partition — your app's managed identity, principal carried explicitly | Multi-day HITL, scheduled/cron work, crash-safe long processes |

Full escalation table, what each tier does *not* have, and cost models: see
[`docs/00-tier-model-and-concepts.md`](docs/00-tier-model-and-concepts.md).

## Document map

| Doc | Covers |
|---|---|
| [`00-tier-model-and-concepts.md`](docs/00-tier-model-and-concepts.md) | Design premises, tier model, escalation table, identity chain overview |
| [`01-gateway-config-and-adapter-contract.md`](docs/01-gateway-config-and-adapter-contract.md) | `apps.yaml`/`upstreams.yaml`, the `UpstreamAdapter` protocol, T1/T2/T3 adapter implementations, version pins |
| [`02-decisions.md`](docs/02-decisions.md) | D1–D10: finalised design decisions with rationale and rejected alternatives |
| [`03-postgres-schema.md`](docs/03-postgres-schema.md) | Full `gw_*` schema, cross-replica event fan-in, Azure Postgres/Entra notes |
| [`04-tier1-prompt-agents.md`](docs/04-tier1-prompt-agents.md) | Prompt agent YAML, workflows, code interpreter, onboarding |
| [`05-tier2-hosted-agents.md`](docs/05-tier2-hosted-agents.md) | Deployment, `azure.yaml`, identity delegation reference implementation, Fabric IQ, multi-agent patterns, progress events |
| [`06-tier3-durable-agents.md`](docs/06-tier3-durable-agents.md) | Deployment, determinism rules, triggers (A2A/cron/Teams), HITL, the three planes |
| [`07-artifacts-and-code-interpreter.md`](docs/07-artifacts-and-code-interpreter.md) | Blob artifact policy, code interpreter container lifecycle, MCP → code interpreter handoff |
| [`08-open-items-and-experiments.md`](docs/08-open-items-and-experiments.md) | Consolidated backlog: empirical checks (⚠) to run before build, open decisions (◆), and every correction made while merging the source drafts |

## Status at a glance

**Decided and specifiable now:** tier model and routing, adapter `Protocol`
interface, Postgres schema, identity delegation pattern (T1/T2), SSE
transport, `input-required` contract, retention policy, linter rule
catalogue, mid-run steering design, agent-version retention, preview/GA
profiles, artifact storage policy, T3 trigger model (A2A-to-A2A + webhook
push, not SSE).

**Not yet verified — run as spikes, don't block scaffolding on them:**
conversation/memory isolation enforcement (T1-ISO-1/2), Fabric IQ passthrough
on hosted agents (T2-FAB-1), cancel semantics/billing, workflow mid-run
injection, trace propagation, concurrent-turn serialisation, payload limits,
T3 session-TTL vs. conversation-retention conflict.

See [`docs/08-open-items-and-experiments.md`](docs/08-open-items-and-experiments.md)
for the full, prioritised list.

## Code layout

```
long-running-agents/
├── docs/                  # the merged plan (see Document map above)
├── src/gateway/           # the gateway itself — Python, FastAPI
│   ├── auth/              # EntraValidator / Principal (docs/00 §5, docs/05 §3)
│   ├── upstream/          # UpstreamAdapter + T1/T2/T3 implementations (docs/01)
│   ├── store/             # gw_context / gw_task / gw_event (docs/03)
│   ├── api/                # A2A surface + T3 webhook receiver
│   ├── config.py          # apps.yaml loader
│   ├── registry.py        # builds adapters from config at startup
│   └── main.py            # FastAPI app, startup health probes
├── migrations/0001_init.sql
├── tests/                 # offline unit tests + Postgres integration tests
├── infra/                 # Bicep + az CLI provisioning (below)
└── docker-compose.yml     # local Postgres
```

This is a working skeleton, not a finished gateway: `tasks/get`, `tasks/cancel`
and the SSE follow endpoint are stubbed with `HTTPException(501, ...)` and a
comment pointing at what to wire up (they need a `gw_task → gw_context` join
that's straightforward but was left as an exercise rather than guessed at).
Everything else — principal validation, the IDOR-safe `authorise_context`,
the session-creation-race fix, the T1/T2/T3 adapters, the Postgres schema —
is implemented and tested.

## Running it locally

```bash
python -m venv .venv && source .venv/bin/activate
make install
make db-up       # docker-compose Postgres
make migrate      # applies migrations/0001_init.sql
cp .env.example .env      # then fill in GATEWAY_TENANT_ID, FOUNDRY_PROJECT_ENDPOINT
cp config/apps.example.yaml config/apps.yaml   # then edit
make run          # uvicorn on :8080, see /healthz
```

```bash
make test          # offline tests always run; Postgres tests skip cleanly
                    # if db-up wasn't run — see tests/conftest.py
make lint
```

Local runs receive no platform user context from Foundry — isolation bugs
in the T2 path are invisible here by design (docs/05 §3.6). Trust the
isolation tests only against a deployed agent.

## Deploying the infra

```bash
cd infra
cp config/variables.bicepparam config/variables.local.bicepparam   # edit: tenant, Foundry endpoint, your objectId
./deploy.sh rg-a2a-gateway-dev westeurope config/variables.local.bicepparam

# apply the schema (uses your own az login, over an Entra token — no passwords)
./scripts/apply-db-migrations.sh rg-a2a-gateway-dev

# build and push the real gateway image, point the Container App at it
./deploy.sh --build rg-a2a-gateway-dev westeurope config/variables.local.bicepparam
```

`infra/main.bicep` provisions: a user-assigned managed identity, Log
Analytics + Application Insights, an Azure Database for PostgreSQL Flexible
Server (Entra-only auth), a Storage account with the shared `artifacts`
blob container and its D5 lifecycle policy, a Key Vault, an Azure Container
Registry, and a Container Apps environment + the gateway Container App.

It deliberately does **not** provision the Foundry project or any agents —
those are a separate deployment (docs/04–06, `azd ai agent init` /
`create_version()`) — and it does **not** grant the gateway's identity
`UserIdentityImpersonation` on any agent, because that permission targets a
resource (the Foundry agent endpoint) that doesn't exist yet at this point.
That's the one genuinely manual step per agent, and it's the single most
important one to not skip (docs/05 §6.2 — skipping it silently degrades
every user into one shared T2 sandbox):

```bash
./infra/scripts/grant-agent-access.sh rg-a2a-gateway-dev \
  $(az cognitiveservices account show -g <foundry-rg> -n <foundry-account> --query id -o tsv)
```

Run it once per Foundry account after deploying agents into it. The
gateway's own startup probe (`FoundryHostedAdapter.health()`) fails
readiness if this grant is missing or hasn't propagated yet, rather than
letting the first real user hit a silent isolation hole.
