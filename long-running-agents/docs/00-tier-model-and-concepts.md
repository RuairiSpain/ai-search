# Tier Model and Concepts

**Scope of the gateway:** the gateway only. Agent orchestration internals are
out of scope for the gateway itself — it fronts them.

---

## 1. Design premises

1. **A2A signals long-running work through task state, not HTTP 202.** Every
   `message/send` returns 200 carrying a `Message` or a `Task`. `mode: long`
   in config is a *server-side default* the client can override via
   `MessageSendConfiguration.blocking`.
2. **Routing is path-based.** A2A has no `agent` field in the request body.
   Each app mounts at `/apps/{app}/` with its own card at
   `/apps/{app}/.well-known/agent-card.json`.
3. **The adapter layer is a churn firewall.** Foundry SDK surfaces move fast.
   Everything version-sensitive lives in `app/upstream/*`; the core never
   imports an Azure package.
4. **Progress fidelity is a declared capability, not an assumption.** The
   gateway reports what it actually has rather than fabricating granularity.

## 2. Tier model

| Tier | Upstream | Progress source | Identity delta |
|---|---|---|---|
| **T1** | Prompt agent, `background=True` | gateway poll loop | conversation ID, gateway-owned (no session, no persistent filesystem) |
| **T2** | Hosted agent | gateway poll loop, or `FINE` via app-emitted events (§5.4 of tier2 doc) | + `x-ms-user-identity` delegation |
| **T3** | MAF + Durable Task | pushed events via webhook callback | app's own managed identity; principal carried explicitly, no platform partition |

### Tier 2 identity — the trap

A hosted agent serves many users from one endpoint, but each session is a
VM-isolated sandbox with its own `$HOME`. Isolation keys off the *caller's*
Entra token — and every gateway call carries the gateway's managed identity.
**Without delegation, all chat users collapse into one shared sandbox.**

Requirements:

- Gateway identity holds
  `Microsoft.CognitiveServices/accounts/AIServices/agents/endpoints/UserIdentityImpersonation/action`
  on the agent. Missing → `403`.
- Send `x-ms-user-identity: <stable-end-user-id>` on every request.
  1–256 chars, only letters, digits and `. _ : - @`.
- Send `Foundry-Features: HostedAgents=V1Preview`. Missing → `403
  preview_feature_required`.
- Derive the value **server-side from the verified inbound token**. Never
  from a client-supplied field. Any service holding the impersonation
  permission can act as any end user.
- The platform does **not** fence delegated users from each other. It only
  separates delegated from non-delegated callers. Per-user session mapping
  is the gateway's responsibility, enforced by a unique constraint in
  Postgres (`gw_context_session_owner`).

Lifecycle to handle: 15-minute idle timeout (compute deprovisioned, state
persisted — expect cold start), 30-day inactivity deletion (state gone —
needs a policy, see D5 in `02-decisions.md`).

> **Blocking check:** container protocol 1.0.0 is blocked after **31 July
> 2026**. Reference agent servers must run protocol 2.0.0
> (`azure-ai-agentserver-core >= 2.0.0b7`). The old caller-supplied
> isolation-key model must not enter this design.

### Tier 2 settles the stickiness question

Session state lives in Foundry keyed by identity, not in a gateway replica.
No gateway→instance affinity is needed for T1/T2; APIM round-robin is safe.
Affinity is only relevant for T3-without-DTS (bring-your-own-compute); with
Durable Task Scheduler, any worker resumes any orchestration, so pinning
defeats the scaling model. **Correction applied during merge:** an earlier
draft configured `affinity: context` on the T3 example app; that has been
removed (see tier3 doc §6.1 and the open-items log).

## 3. What each tier does *not* have

**Tier 1** — no container, no sandbox, no `$HOME`, no session, no
`agent_session_id`. Durable state lives only in Foundry conversations
(optionally Cosmos-backed) and preview memory. Artifacts come from
code-interpreter *container* files (~1h TTL), not a session store — see
`07-artifacts-and-code-interpreter.md`.

**Tier 2** — no traffic splitting (one agent endpoint = one version live at
once; canary means deploying a second agent *name* and splitting at the
gateway). No native trace guarantee end-to-end (unverified — see open
items). No filesystem durability past 30-day session deletion.

**Tier 3** — no platform-enforced per-user isolation at all (§3.2 of
tier3 doc): everything downstream runs as the Function App's own managed
identity, so the principal must be carried explicitly and used as a
partition key everywhere you write. No SSE upstream — the T3 A2A server
serves plain HTTP and **pushes** status to the gateway via webhook instead
of holding a stream.

## 4. Escalation decision table

Give this to developers. Any single "yes" means tier 1 is the wrong home.

| Question | Yes → |
|---|---|
| Does it need custom code or business logic? | **T2** |
| Does it need artifacts that outlive a single response (a document the user returns to tomorrow)? | **T2** — a chart returned inside the same turn is fine on T1, since the gateway copies code-interpreter output to blob before the container expires |
| Does it need a private framework (LangGraph, Copilot SDK)? | **T2** |
| Must it pause for approval measured in hours or days? | **T3** |
| Must completed steps survive a crash without re-running? | **T3** |
| Is it scheduled/cron/event-driven with no interactive turn? | **T3** |
| Everything else | **T1** |

**Corrections applied during merge, relative to the original table:**

- *"Does it emit downloadable files or need a filesystem?" → T2* was too
  broad. Code interpreter gives T1 a real (if fragile) artifact channel — see
  `07-artifacts-and-code-interpreter.md`. The row now turns on artifact
  *durability*, not mere file emission.
- *"Does the UI need per-step progress narration?" → T3* is stale. T2 can
  emit its own `gw.progress.v1` events from application code and reach
  `FINE` fidelity without T3's infrastructure (tier2 doc §5.4). That was
  the main remaining argument for defaulting to T3, and it no longer holds.
  Some apps currently pointed at T3 belong in T2 — rewrite existing
  escalation guidance that still cites this row.

Tier 1 has one advantage worth stating: prompt agents support the responses
protocol by default, so **every one of them can be exposed as an A2A
endpoint**. Tier 1 is the cheapest tier for the gateway to front, both in
compute cost and in adapter complexity.

## 5. Identity chain — end to end (T1/T2)

```
┌─────────────┐
│  Chat UI    │  MSAL token, scope = api://a2a-gateway/.default
│             │  (the UI has NO Foundry relationship)
└──────┬──────┘
       │ Authorization: Bearer <user token, audience = gateway>
       ▼
┌─────────────┐
│ A2A Gateway │  validate signature, issuer, audience, expiry
│             │  principal.subject = "{tid}.{oid}"
│             │  authorise contextId against principal   <-- IDOR control
└──────┬──────┘
       │ Authorization: Bearer <GATEWAY managed identity token,
       │                        audience = https://ai.azure.com>
       │ x-ms-user-identity: {tid}.{oid}        <-- opaque string, NOT a token
       │ Foundry-Features: HostedAgents=V1Preview   (T2 only)
       ▼
┌─────────────┐
│   Foundry   │  routes to session partitioned by the opaque string (T2)
│  Responses  │  returns agent_session_id (T2)
└──────┬──────┘
       │ container receives platform user context + call context (T2)
       ▼
┌─────────────┐
│  Container  │  runs as the AGENT identity (its own Entra ID)   [T2 only]
│             │  $HOME is private to this session
└──────┬──────┘
       │ Toolbox MCP endpoint, per-connection auth:
       │   AgenticIdentity  -> agent identity
       │   UserEntraToken   -> END USER's token
       │   OAuth2           -> stored per-user OAuth token
       ▼
   downstream systems
```

### What each layer proves

| Layer | Credential | Proves |
|---|---|---|
| UI → gateway | user's Entra token, gateway audience | who the human is |
| gateway → Foundry | gateway managed identity | the gateway may call this agent |
| gateway → Foundry | `x-ms-user-identity` (header) | *which partition* — not identity |
| container → models | agent identity | the agent may infer |
| container → tools | per-connection authType | varies — this is the only per-user dial |

### The two things people conflate

**Isolation ≠ execution context.** The sandbox is partitioned per user; the
process inside runs as the agent. `x-ms-user-identity` is an opaque string
with no cryptographic content — it cannot authorise anything downstream.

**Delegation ≠ passthrough.**

| | Header delegation | Identity passthrough |
|---|---|---|
| carries | opaque string | real user token |
| gives you | session + `$HOME` + conversation isolation | downstream per-user authorisation |
| user needs Foundry RBAC | **no** | **yes** — Foundry Agent Consumer |
| cross-tenant / B2C | works | **not supported** |
| consent UX | none | consent link the user must complete |

Use delegation on every T2 app. Use passthrough only on the specific
connections that enforce per-user permissions (e.g. Fabric IQ — see
`05-tier2-hosted-agents.md` §4).

Full reference implementation (principal extraction, adapter headers, IDOR
test suite) is in `05-tier2-hosted-agents.md`. T3's very different identity
story (no partition at all) is in `06-tier3-durable-agents.md` §3.
