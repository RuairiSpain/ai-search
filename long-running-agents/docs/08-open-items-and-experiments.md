# Open Items, Experiments, and Merge Corrections

This is the consolidated backlog. Every ⚠/◆ scattered across the individual
docs is collected here so nothing gets lost between them, plus a record of
where the source drafts disagreed with each other and which position won.

## A. Empirical checks to run before trusting the design (⚠)

These are spikes, not design work — the decisions doc's own framing is
right: "run them in a scratch project this week; several of them can
change the design." None of them block scaffolding the project structure,
CI, schema, or adapter interfaces — they block *trusting* specific
capability claims.

| # | Check | Blocks | Doc |
|---|---|---|---|
| 1 | **T1-ISO-1** — does `x-ms-user-identity` actually scope conversation reads, or is `gw_context` the entire boundary? | D1 review weight | `02-decisions.md` D1 |
| 2 | **T1-ISO-2** — is user/procedural memory isolated per identity? | D2, possible simplification (delete gateway-side memory store) | `02-decisions.md` D2 |
| 3 | **T2-FAB-1** — can a hosted-agent container consume `UserEntraToken` passthrough and complete a consent flow, or does it hit `AADSTS50013`? | The single highest-leverage check in the whole plan — a fail **inverts the escalation table** (per-user Fabric access becomes T1-only) | `05-tier2-hosted-agents.md` §4.3 |
| 4 | Cancel endpoint semantics: shape, billing effect, code-interpreter-container effect | `Capabilities.cancel` for T1 stays feature-flagged until verified | `02-decisions.md` D7 |
| 5 | T1 workflow mid-run injection — does `SetVariable` re-read actually see appended conversation items? | D7 feasibility for workflow-level steering | `02-decisions.md` D7 |
| 6 | A2A protocol version: does `message/send` accept a task in `working` state, or only `input-required`? | If disallowed, interjections need a gateway-local endpoint, not the standard message path | `02-decisions.md` D7 |
| 7 | T2 container sizing: 0.5/1/2 vCPU-GiB list vs. 0.25–4.0 vCPU / 0.5–8.0 GiB range — both documented as current | Capacity planning | `05-tier2-hosted-agents.md` §1 |
| 8 | Model quota/TPM: per-deployment or shared across a project's agents? | Whether noisy T2 apps need dedicated deployments | `05-tier2-hosted-agents.md` §6.2 |
| 9 | W3C `traceparent` propagation gateway → Responses call → container span (T2) and gateway → A2A → orchestration → activity (T3) | End-to-end debuggability; **the gap to close first**, per both tier docs | `05-tier2-hosted-agents.md` §6.3, `06-tier3-durable-agents.md` §6.3 |
| 10 | Does the platform serialise concurrent requests to one `agent_session_id`? | Serialise per session in the gateway regardless — but confirm the failure mode if you don't | `05-tier2-hosted-agents.md` §6.3 |
| 11 | Cold-start restore time after 15-minute idle deprovision | Gateway timeout and reaper lease durations must exceed worst case | `05-tier2-hosted-agents.md` §6.3 |
| 12 | Payload/upload limits on `/responses` and `/files` | The MCP-data-to-code-interpreter path with real datasets | `05-tier2-hosted-agents.md` §6.3 |
| 13 | Three-way A2A version matrix: gateway's A2A target × `agent-framework-a2a`'s `a2a-sdk` pin × Foundry's 1.0/0.3 support | T3 module layout | `06-tier3-durable-agents.md` §6.2 |
| 14 | Python surface parity for `agent-framework-azurefunctions` / `agent-framework-durabletask` — Learn docs skew C# | Correctness of every T3 code sample | `06-tier3-durable-agents.md` §2.2 |
| 15 | Does durable large-payload offload cover **entity state**, or only orchestration inputs/outputs as documented? | Whether it actually helps durable agent sessions (built on entities) | `06-tier3-durable-agents.md` §6.3 |
| 16 | Container delete API (`DELETE /v1/containers/{id}`) exposed on Foundry's `/openai/v1/containers/` path for your API version | Whether explicit reclaim is even reachable | `07-artifacts-and-code-interpreter.md` §3 |
| 17 | Real cost of code-interpreter container idle time — guidance suggests no separate charge | Whether reclaim engineering is worth doing at all | `07-artifacts-and-code-interpreter.md` §3 |

## B. Open design decisions (◆) — not yet made

| # | Item | Doc |
|---|---|---|
| 1 | Gateway-as-traffic-splitter mechanism for T2 canary (deploy `-v2` as a separate agent name; `gw_context` must pin agent name, not just version) | `05-tier2-hosted-agents.md` §6.1 |
| 2 | Region topology — where blob/gateway/hosted-agent regions must match | `05-tier2-hosted-agents.md` §6.1 |
| 3 | Registry drift detection (deleted/renamed/redeployed Foundry agent) | `05-tier2-hosted-agents.md` §6.2 |
| 4 | RBAC provisioning automation for `UserIdentityImpersonation` | `05-tier2-hosted-agents.md` §6.2 |
| 5 | Version cutover runbook automation (steps are written; tooling isn't) | `05-tier2-hosted-agents.md` §6.2 |
| 6 | Cost attribution export pipeline | `05-tier2-hosted-agents.md` §6.2 |
| 7 | Session/conversation divergence UX (T2 session dies at 30d idle; conversation retention is a separate 30d sliding clock) | `05-tier2-hosted-agents.md` §6.3 |
| 8 | T3 hosting model — Flex Consumption is *recommended*, not locked | `06-tier3-durable-agents.md` §2.5 |
| 9 | T3 cancel-vs-terminate contract (per app, on the card) | `06-tier3-durable-agents.md` §6.1 |
| 10 | T3 DTS RBAC grants in the provisioning pipeline | `06-tier3-durable-agents.md` §6.2 |
| 11 | T3 instance ID scheme + purge policy alignment with `gw_task` (D5: 90d) | `06-tier3-durable-agents.md` §6.2 |
| 12 | **T3 session TTL (14 days) vs. D5 conversation retention (30 days sliding)** — a user returning on day 20 gets history with no session behind it. Needs a per-tier retention override, a keep-alive, or an honest UI downgrade. Resolve before the first month-long T3 app ships. | `06-tier3-durable-agents.md` §6.3, `02-decisions.md` D5 |
| 13 | Multi-day HITL: a 45-day approval outliving 30-day context retention | `06-tier3-durable-agents.md` §5.3 |
| 14 | Blob immutability requirement for artifacts of record | `02-decisions.md` D5, `07-artifacts-and-code-interpreter.md` §2 |
| 15 | Per-app artifact retention period beyond the D5 default | `07-artifacts-and-code-interpreter.md` §2 |
| 16 | APIM + SSE: response buffering and the 240s default timeout — v2 concern, note now so v1 doesn't get painted into a corner | `02-decisions.md` D3 |

## C. Already resolved by a later draft — corrections applied while merging

The source material arrived as multiple revisions of the same documents.
Where a later revision silently corrected an earlier one, this plan kept
only the final position. Recorded here so nobody re-opens a settled
question by reading an old copy:

1. **T3 upstream protocol.** Early adapter-spec drafts (and the decisions
   doc's own "open items" table) listed "A2A-to-A2A vs REST+callback" as
   unresolved. The T3 guide resolves it: **A2A-to-A2A, with the gateway's
   `gw_task` as system of record, and the T3 A2A server pushing status via
   webhook rather than holding an SSE connection.** Any reference to this
   as still-open is stale.
2. **T2 progress fidelity.** The T2 guide's own "three planes" table
   listed "T2 progress fidelity → FINE" as "◆ decide before samples." The
   informal outstanding-items note is explicit that this is wrong: §5.4
   (app-emitted `gw.progress.v1` events) **is the decision**, not an
   experiment. Marked decided in `05-tier2-hosted-agents.md` §6.1.
3. **T3 `affinity: context`.** An early gateway-config example pinned
   worker affinity for the T3 upstream. The T3 guide corrects this:
   Durable Task Scheduler means any worker can resume any orchestration,
   so affinity only applies to non-DTS bring-your-own-compute. Removed
   from the config schema in `01-gateway-config-and-adapter-contract.md`.
4. **T1 `Capabilities.artifacts`.** An early adapter-spec draft set this
   `False` on the reasoning that prompt agents have no filesystem. A later
   note corrects it: code interpreter gives T1 a real, if fragile,
   artifact channel with a ~1-hour container lifecycle. Both the final
   adapter contract and the escalation table reflect `True` plus the
   gateway's harvest obligation (`07-artifacts-and-code-interpreter.md`).
5. **Escalation table, progress row.** "Does the UI need per-step
   narration → T3" is stale for the same reason as #2 above — T2 can reach
   `FINE`. Reworded in `00-tier-model-and-concepts.md` §4; some apps
   currently pointed at T3 on this basis belong in T2.
6. **D7 mid-run steering.** An early decisions draft proposed a simple
   `IMPORTANT ***`-prefixed injection with only a security-envelope
   critique attached, and left tier-by-tier feasibility largely
   unaddressed. The final version replaces it with the tier-dependent
   steering table (T1 none/deferred, T1-workflow/T2/T3 checkpoint via
   different mechanisms), the `steer()` adapter method, `SteerResult`, and
   the `gw_interjection` table. The security envelope rules carried
   forward unchanged into the final version.
7. **Adapter contract `UpstreamRef`.** Early drafts lacked `container_id`.
   Added once the code-interpreter container lifecycle work made explicit
   container mode (vs. `type: auto`) the recommended pattern.
8. **A2A gateway spec / decisions doc revision count.** Both documents
   arrived in two near-identical generations; the only functional deltas
   between them are items 4, 6 and 7 above. No other content differences
   were found between revisions of the same document.

## D. Duplicate source documents collapsed during merge

For traceability: these upload sets were identical or near-identical
copies of the same document and were merged into one position in this
plan rather than kept as separate files.

- `t2-hosted-agents-guide.md` — 3 identical copies → `05-tier2-hosted-agents.md`
- `t2-identity-delegation.md` — 2 identical copies → folded into `05-tier2-hosted-agents.md` §3
- `a2a-gateway-adapter-spec.md` — 3 revisions (2 identical latest + 1 earlier draft, see C.7) → `01-gateway-config-and-adapter-contract.md`
- `a2a-gateway-decisions.md` — 2 revisions (see C.6) → `02-decisions.md`
- `a2a_t2_outstanding.md` — 2 identical copies (informal commentary) → content folded into `05-tier2-hosted-agents.md` §4.3 and the C.2 correction above
- `a2a-code-interpreter-use-container-with-shorter-timeout.md`, `a2a-save-artifacts.md`, `a2a-t1-code-interpreter.md` — three informal working notes, single copies each → merged into `07-artifacts-and-code-interpreter.md`
- `t3-durable-agents-guide.md` — single copy → `06-tier3-durable-agents.md`
