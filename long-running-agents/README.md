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
