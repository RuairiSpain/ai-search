# Long-Running Agents — A2A Gateway on Microsoft Foundry

An A2A ([Agent2Agent protocol](https://a2a-protocol.org/)) gateway, built on
`a2a-sdk`, that fronts Microsoft Foundry **hosted agents (T2)** and
**Durable-Task orchestrations (T3)** behind one uniform, spec-conformant
task/message contract, for one or more chat clients.

A third Foundry agent shape, prompt agents (T1), is deliberately **not**
fronted by this gateway — see "Why three tiers, two gateway tiers" below.

This folder is the merged, de-duplicated project plan assembled from thirteen
source documents (some in 2–3 revisions) written during design. Where later
revisions corrected or superseded earlier ones, this plan keeps only the
final position and notes the correction. See
[`docs/08-open-items-and-experiments.md`](docs/08-open-items-and-experiments.md)
for a full list of what changed between drafts — including, later, what
changed once the gateway was built and run against a real `a2a-sdk` and a
real Postgres (section E).

## Why three tiers, two gateway tiers

| Tier | What runs | Owns orchestration | Identity model | Typical use |
|---|---|---|---|---|
| **T1 — Prompt agent** *(not fronted by this gateway)* | Stateless model+tools call on Foundry's managed inference plane | Foundry (or a portal Workflow) | Conversation ID, Foundry-owned | No-code agents, cheapest to run and to front |
| **T2 — Hosted agent** | Your container, in a per-session VM-isolated Foundry sandbox with persistent `$HOME` | Your code (Microsoft Agent Framework) | `x-ms-user-identity` delegation into a per-user sandbox | Custom logic, multi-agent orchestration, file output, sub-hour work |
| **T3 — Durable agent** | Azure Durable Functions / Durable Task, MAF + `a2a-sdk` | Your code, deterministic orchestrators | No platform partition — your app's managed identity, principal carried explicitly | Multi-day HITL, scheduled/cron work, crash-safe long processes |

T1 doesn't need anything this gateway adds: no per-user sandbox to multiplex,
no streaming beyond a single response, no artifacts channel beyond
code-interpreter citations. Foundry already exposes T1 agents over its own
native, incoming A2A endpoint — point T1 clients there directly, or expose a
T1 agent as an MCP server / short-lived agent (e.g. for M365/Teams) instead
of routing it through this gateway. `config/apps.yaml` here only ever
declares `tier: t2` or `tier: t3`.

Full escalation table, what each tier does *not* have, and cost models: see
[`docs/00-tier-model-and-concepts.md`](docs/00-tier-model-and-concepts.md).

## Document map

| Doc | Covers |
|---|---|
| [`00-tier-model-and-concepts.md`](docs/00-tier-model-and-concepts.md) | Design premises, tier model, escalation table, identity chain overview |
| [`01-gateway-config-and-adapter-contract.md`](docs/01-gateway-config-and-adapter-contract.md) | `apps.yaml`/`upstreams.yaml`, the `UpstreamAdapter` protocol, T2/T3 adapter implementations, version pins, and §4: the gateway's `a2a-sdk` integration |
| [`02-decisions.md`](docs/02-decisions.md) | D1–D10: finalised design decisions with rationale and rejected alternatives |
| [`03-postgres-schema.md`](docs/03-postgres-schema.md) | Full `gw_*` schema, cross-replica event fan-in, Azure Postgres/Entra notes |
| [`04-tier1-prompt-agents.md`](docs/04-tier1-prompt-agents.md) | *Out of scope for this gateway* — prompt agent YAML, workflows, code interpreter, onboarding, kept as reference for T1's own front door |
| [`05-tier2-hosted-agents.md`](docs/05-tier2-hosted-agents.md) | Deployment, `azure.yaml`, identity delegation reference implementation, Fabric IQ, multi-agent patterns, progress events |
| [`06-tier3-durable-agents.md`](docs/06-tier3-durable-agents.md) | Deployment, determinism rules, triggers (A2A/cron/Teams), HITL, the three planes |
| [`07-artifacts-and-code-interpreter.md`](docs/07-artifacts-and-code-interpreter.md) | Blob artifact policy, code interpreter container lifecycle, MCP → code interpreter handoff |
| [`08-open-items-and-experiments.md`](docs/08-open-items-and-experiments.md) | Consolidated backlog: empirical checks (⚠) to run before build, open decisions (◆), and every correction made while merging the source drafts |

## Status at a glance

**Decided and specifiable now:** tier model and routing, adapter `Protocol`
interface, Postgres schema, identity delegation pattern (T2), the gateway's
`a2a-sdk`-based client-facing surface, `input-required` contract, retention
policy, linter rule catalogue, mid-run steering design, agent-version
retention, preview/GA profiles, artifact storage policy, T3 trigger model
(A2A-to-A2A + webhook push, not SSE).

**Not yet verified — run as spikes, don't block scaffolding on them:**
conversation/memory isolation enforcement (ISO-1/2), Fabric IQ passthrough
on hosted agents (T2-FAB-1), cancel semantics/billing, trace propagation,
concurrent-turn serialisation, payload limits, T3 session-TTL vs.
conversation-retention conflict.

See [`docs/08-open-items-and-experiments.md`](docs/08-open-items-and-experiments.md)
for the full, prioritised list.

## Code layout

```
long-running-agents/
├── docs/                  # the merged plan (see Document map above)
├── src/gateway/           # the gateway itself — Python, FastAPI
│   ├── auth/              # EntraValidator / Principal (docs/00 §5, docs/05 §3)
│   ├── upstream/          # UpstreamAdapter + T2/T3 implementations (docs/01)
│   ├── store/              # gw_context / gw_task / gw_event / gw_artifact (docs/03)
│   ├── a2a_server/          # client-facing A2A surface, built on a2a-sdk:
│   │                        # AgentExecutor, TaskStore adapter, agent card,
│   │                        # FastAPI mounting (docs/01 §4) — one mount per
│   │                        # T2/T3 app, plus the T3 webhook receiver
│   ├── artifacts.py        # ArtifactHarvester: container files -> blob -> SAS (docs/07)
│   ├── config.py          # apps.yaml loader
│   ├── registry.py        # builds adapters + harvester from config at startup
│   └── main.py            # FastAPI app, startup health probes
├── migrations/0001_init.sql
├── tests/                 # offline unit tests + Postgres integration tests
├── infra/                 # Bicep + az CLI provisioning (below)
└── docker-compose.yml     # local Postgres
```

This is a working skeleton, not a finished gateway. What's implemented and
tested end to end (`SendMessage` → `GetTask` → `CancelTask`, through the
real `a2a-sdk`-mounted routes against a real Postgres): principal
validation, the IDOR-safe `authorise_context`/`get_or_create_context`, the
session-creation-race fix, the T2/T3 adapters, the full spec-conformant A2A
surface, T2 code-interpreter artifact harvesting (copy to the shared
blob container, index in `gw_artifact`, download via a short-lived
user-delegation SAS), and inbound file parts (`Part.raw`/`Part.url`
extracted and forwarded — T2 uploads via the Files API and references the
resulting `file_id`; T3 relays the part to its own upstream A2A server —
see `01-gateway-config-and-adapter-contract.md` §5).

**Known gaps, not hidden:**
- `steer()` and steering into a `working` task — the adapter methods exist,
  nothing exposes them over the A2A surface yet, and `gw_interjection` is
  unused. Same for T2's `resume()`.
- A client-side "blind retry" (same `messageId`, no `taskId`, because the
  original response was lost) can't be transparently resolved to the
  original task under `a2a-sdk`'s per-request task-identity model — it's
  now rejected with a clear error instead of silently misrouted or
  double-submitted. See docs/08 item E.6.
- T3 artifacts still download through their native mechanism, not the
  shared blob container — only T2's code-interpreter citation path is
  harvested in this pass. See docs/07 §2 item 3 and docs/08.
- `DurableAdapter`'s JSON-RPC wire format was corrected to match the real
  `a2a-sdk` (method names, `Part` shape, task-state vocabulary — it was
  wrong before, see docs/08 item E.7), but no real T3 A2A server has been
  run against this gateway yet. Verified only as far as "parses correctly
  against the installed a2a-sdk's own `ParseDict`," not against actual T3
  behavior.
- The reaper (`gw_task_reaper`, `TaskStore.reap_wedged_tasks`) exists but
  nothing schedules it.
- `gw_push_config` is defined but never read or written.
- Orphan upstream-session cleanup after a lost session-creation race is a
  logged warning, not an actual termination call.
- `gwlint` (the D6 CI linter) doesn't exist as code.
- No VNet/private endpoint on Postgres or storage (deliberately deferred).

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
