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
| 1 | **ISO-1** — does `x-ms-user-identity` actually scope conversation reads, or is `gw_context` the entire boundary? | D1 review weight | `02-decisions.md` D1 |
| 2 | **ISO-2** — is user/procedural memory isolated per identity? | D2, possible simplification (delete gateway-side memory store) | `02-decisions.md` D2 |
| 3 | **T2-FAB-1** — can a hosted-agent container consume `UserEntraToken` passthrough and complete a consent flow, or does it hit `AADSTS50013`? | The single highest-leverage check in the whole plan — a fail **inverts the escalation table** (per-user Fabric access becomes T1-only) | `05-tier2-hosted-agents.md` §4.3 |
| 4 | Cancel endpoint semantics: shape, billing effect, code-interpreter-container effect | `Capabilities.cancel` for T2 (the only gateway tier built on `FoundryResponsesAdapter`) stays feature-flagged until verified | `02-decisions.md` D7 |
| 5 | T1 workflow mid-run injection — does `SetVariable` re-read actually see appended conversation items? | D7 feasibility for workflow-level steering | `02-decisions.md` D7 |
| 6 | ~~A2A protocol version: does `message/send` accept a task in `working` state, or only `input-required`?~~ **Resolved:** disallowed (confirmed against the spec building `a2a_server/executor.py`, Phase 3). The gateway-local endpoint this predicted is built — see item E.9. | ~~If disallowed, interjections need a gateway-local endpoint~~ | `02-decisions.md` D7 |
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

## E. Corrections applied after the initial build (a2a-sdk adoption, T1 removed from the gateway, bidirectional files, closing the remaining known gaps)

Unlike section C, these weren't found while merging source drafts — they
were found building against the real `a2a-sdk` package and a real Postgres,
after the initial hand-rolled-router version of the gateway already worked.
Recorded for the same reason as section C: don't re-discover these the hard
way from a stale doc.

1. **T1 removed as a gateway tier.** `src/gateway/config.py`'s
   `AppConfig.tier`/`UpstreamConfig.tier` are now `Literal["t2", "t3"]`;
   `registry.py` no longer builds a T1 adapter; `api/a2a.py` (the old
   hand-rolled JSON-RPC router) is deleted. T1 is fronted by Foundry's own
   native incoming A2A endpoint instead — see `00-tier-model-and-concepts.md`
   and `04-tier1-prompt-agents.md`'s new scope banner. `T1-ISO-1`/`T1-ISO-2`
   were renamed to `ISO-1`/`ISO-2` throughout (they're platform-behavior
   checks, not really tier-specific — see item A.1/A.2 above).
2. **The gateway's client-facing A2A surface now runs on `a2a-sdk`**
   (`src/gateway/a2a_server/`), replacing the hand-rolled JSON-RPC dispatch.
   Scoped to T2/T3 only, per item 1. See `01-gateway-config-and-adapter-contract.md`
   §4 for what this bought and the three integration pitfalls below — none
   of them are in any SDK changelog or docstring; each was found only by
   running a real client (or a test standing in for one) against the
   mounted routes.
3. **Missing `A2A-Version` header on the call context.** A custom
   `ServerCallContextBuilder` that doesn't populate `state["headers"]` gets
   every request treated as protocol 0.3 and rejected — the SDK's own
   `DefaultServerCallContextBuilder` does this, but nothing forces a custom
   builder to. `GatewayCallContextBuilder` was missing it, so every real
   client call would have failed: a clean HTTP 200 wrapping a JSON-RPC
   `VersionNotSupportedError`, easy to miss in casual testing since nothing
   about the transport layer complains. Fixed in
   `src/gateway/a2a_server/context.py`.
4. **Duplicate initial-task creation.** The executor created the `gw_task`
   row directly (so it can carry `app`/`tier`, which aren't part of the
   generic A2A `Task` schema) *and* enqueued an SDK `new_task()` event for
   the same id — which the SDK's own `TaskManager` sees as a second,
   redundant creation and logs as an error ("Task already exists, ignoring
   task replacement") on every single send. Fixed by dropping the redundant
   enqueue: the direct DB row is sufficient for the SDK's own requirement
   (a row must exist before it will accept a `TaskStatusUpdateEvent`), it
   never needed a matching `Task` event too.
5. **Cancellation ordering.** `a2a-sdk`'s `ActiveTask.cancel()`
   force-cancels the task's running `AgentExecutor.execute()` coroutine
   *before* awaiting `AgentExecutor.cancel()`. A design (the original one
   here) that expects the `follow()` loop to observe and persist the
   upstream's cancellation confirmation never gets the chance — and writing
   the terminal state through `TaskUpdater`/the event queue from inside
   `cancel()` doesn't work either, because the producer's own teardown may
   be concurrently closing that exact queue (confirmed via the SDK's own
   log line: "Queue was closed during enqueuing. Event dropped."). Fixed by
   having `cancel()` persist the terminal state directly against the store,
   right after `adapter.cancel()` confirms — see the D7 implementation note
   in `02-decisions.md`.
6. **Per-request task identity breaks blind retries.** `a2a-sdk` mints a
   fresh `task_id` per `message/send` whenever the client's message omits
   one; there's no supported way to redirect that request's `ActiveTask` to
   a *different*, already-existing task after the fact (`TaskManager`
   rejects an event whose id doesn't match the id it was constructed with).
   The original dedupe-retry design assumed the gateway could hand a
   client-blind retry back its original task under a fabricated event —
   confirmed to crash the request instead. Fixed: the upstream submission
   is still never repeated (D7's actual idempotency property, unaffected),
   but a retry with no `taskId` is now rejected with a clear error rather
   than misrouted. Clients that want idempotent retries need to supply
   their own `taskId` up front. Open item: no gateway-side workaround for a
   client that truly cannot do this exists yet.
7. **`DurableAdapter`'s wire format predated Phase 3's a2a-sdk
   verification and was never corrected.** `submit()`/`cancel()` used
   `"method": "message/send"`/`"tasks/cancel"` (old A2A method-name
   convention) and a `kind`-discriminated `Part` shape, neither of which
   `a2a-sdk`'s real JSON-RPC dispatcher accepts (it wants PascalCase
   `SendMessage`/`CancelTask`, and `Part` has no `kind` field). The response
   parser also compared task state against the wrong vocabulary — plain
   lowercase strings like `"submitted"` against what the SDK actually
   returns, `"TASK_STATE_SUBMITTED"`. Every T3 call would have failed
   end-to-end against a real T3 upstream built on `agent-framework-a2a`.
   Found and fixed while wiring in file-part support (Phase 4, item 8
   below), not by a dedicated T3 review — no real T3 A2A server has been
   run against this gateway yet, so this fix is verified only as far as
   "the request now parses correctly against the installed a2a-sdk's own
   `ParseDict`," not against an actual T3 server's behavior.
8. **Bidirectional files (Phase 4).** Inbound `Part.raw`/`Part.url` are now
   extracted and passed to `UpstreamAdapter.submit()`/`resume()` as
   `InboundFile`s. T2 uploads via the Files API and references the
   resulting `file_id` in the Responses input; T3 relays the part as-is to
   its own upstream A2A server. See `01-gateway-config-and-adapter-contract.md`
   §5. Only inbound was in scope for this phase — outbound T3 artifacts
   still go through T3's native mechanism rather than the shared blob
   container, a pre-existing gap this phase didn't touch.

**Phase 5 — closing the remaining known gaps.** The README's "known gaps"
list (steering, T2 `resume()`, T3 artifact harvesting, the reaper,
`gw_push_config`, orphan-session cleanup, `gwlint`) turned out not to be
independent items — building and actually verifying each one surfaced
real bugs in already-shipped code that no amount of code review had
caught, because nothing had exercised these paths against anything more
real than a `FakeAdapter`. In order found:

9. **Mid-run steering, exposed.** A2A has no client-initiated-message-into-
   a-`working`-task concept (confirmed against the spec while building
   `a2a_server/executor.py` back in Phase 3), so steering needed a
   gateway-owned side channel rather than an A2A method:
   `POST /apps/{app}/tasks/{task_id}/interject`
   (`a2a_server/interjections.py`), same IDOR posture as every other
   task-scoped endpoint, backed by a new `InterjectionStore` writing to
   `gw_interjection` (the table already existed, unused, since the
   original schema design).
10. **`TaskStore.append_event()` never updated `gw_task.state` for a
    non-final transition.** Only a `final` status event (completed/failed/
    canceled/rejected) touched the `state` column — a genuinely `working`
    task stayed reported as `submitted` for its entire in-flight lifetime,
    every single time, since the gateway first started polling/relaying
    events. Found via the interject endpoint's own "is this task actually
    working" check, which could never observe `working` because of it.
    Every other test that touched task state only ever checked a terminal
    state, which is exactly why this went unnoticed through two prior
    phases of testing.
11. **`FoundryHostedAdapter` never called `FoundryResponsesAdapter.__init__`,
    so `self._openai` was unconditionally `None`.** `follow()`, `steer()`,
    `cancel()`, and the newly-implemented `resume()` are all *inherited*
    from `FoundryResponsesAdapter` and reference `self._openai` directly —
    every one of them would have raised `AttributeError` the first time it
    ran against a real T2 task. `fetch_artifact_bytes()` had the matching
    problem with `self._project_endpoint`/`self._credential`, never set at
    all on this class. Fixed by making `_openai` a property (a fresh
    per-call client, matching T2's actual client lifecycle) and plumbing
    the two missing constructor params through from the registry. Never
    caught because every test exercising the A2A surface used a
    `FakeAdapter` standing in for the whole `UpstreamAdapter` Protocol,
    never this class itself — a new offline test
    (`tests/test_foundry_hosted_adapter.py`) now exercises the real class
    directly specifically to keep this from recurring.
12. **`a2a-sdk`'s REST routes always include an undocumented
    `Mount(path='/{tenant}', ...)` multi-tenancy catch-all** (regex
    `^/(?P<tenant>[^/]+)/(?P<path>.*)$`, present in `create_rest_routes()`'s
    output regardless of whether tenancy is used) whose path pattern
    matches almost any 2+-segment path. Starlette tries routes in
    registration order and a matching `Mount` fully delegates rather than
    falling through, so the interject route — registered after
    `add_a2a_routes_to_fastapi()` in the first draft — 404'd on every
    single call, silently, because the catch-all Mount claimed the match
    first and its own sub-app didn't recognise the path. Root-caused only
    by bisecting real HTTP requests through progressively smaller route
    sets (`app.routes` introspection alone didn't reveal it — everything
    LOOKED registered correctly). Fixed by registering gateway-owned
    routes *before* `add_a2a_routes_to_fastapi()`, not after.
13. **`DurableAdapter` never sent the `A2A-Version` header on its outbound
    calls to the T3 upstream.** Standing up a *real* `a2a-sdk`
    `DefaultRequestHandler` as a T3 test double (`tests/test_durable_adapter_wire_format.py`
    — stronger verification than item 7's `ParseDict`-only check, since it
    exercises the SDK's actual server-side dispatch) immediately rejected
    every request with `VERSION_NOT_SUPPORTED`: the same header-omission
    bug as item 3 above, just on the outbound side this time, and missed
    by the item-7 fix because `ParseDict` alone can't detect a header the
    request handler needs but the parser doesn't. This is the concrete
    payoff of building a real test double instead of trusting isolated
    parsing checks.
14. **T3 artifact harvesting, orphan-session termination, the reaper
    schedule, and `gwlint`** are now real: `DurableAdapter.fetch_artifact_bytes()`
    fetches a `download_url` the orchestrator supplies in `upstream_ref`
    and harvests through the same `ArtifactHarvester` T2 uses;
    `FoundryHostedAdapter.terminate_session()` calls the real, documented
    `AgentsOperations.stop_session` (confirmed present on
    `AIProjectClient.agents` in the installed `azure-ai-projects` package —
    not a guessed REST endpoint, unlike some other integration points in
    this codebase); `main.py`'s lifespan now runs a background sweep
    calling `TaskStore.reap_wedged_tasks` on a timer, with lease
    renewal wired into task creation and every relayed event
    (`TaskStore.renew_lease`, `AppConfig.lease_seconds`); and `gwlint`
    (`src/gateway/gwlint.py`) implements the D6 safety rules actually
    checkable from this repo alone (L020, L022, L023, L030, L032),
    reporting every other rule as `SKIP` with a reason rather than
    silently omitting it.

15. **`samples/` added, and a real narration bug found + fixed while
    building it.** `GatewayAgentExecutor._follow_and_relay`
    (`src/gateway/a2a_server/executor.py`) called
    `TaskUpdater.update_status(state)` with no `message` for every event of
    every tier — `StatusEvent.detail`, the `gw.progress.v1` narration text
    computed and stored in `gw_event`, never reached the A2A wire. Fixed by
    building a `Message` via `a2a.helpers.proto_helpers.new_text_message`
    when `detail` is set and passing it through, verified against the real
    installed `a2a-sdk` package (`TaskUpdater.update_status`'s
    `message: Message | None` parameter) before writing the fix, not
    assumed. Found while building `samples/tier3/01-durable-hello-world-status`,
    whose whole premise depends on a client actually seeing narration text.
    Building the T2 counterpart (`samples/tier2/04-long-running-hello-world`)
    surfaced the mirror-image, NOT-fixed gap: `FoundryResponsesAdapter.follow()`
    never populates `StatusEvent.detail` in the first place, so
    `FoundryHostedAdapter.capabilities`'s claim of "COARSE promoted to FINE
    by the gw.progress.v1 filter" (docs/05 §5.4) describes a decision that
    was never implemented, not current behavior. Left undone here — parsing
    custom events out of a Foundry Responses poll loop is materially bigger
    and touches an unverified part of the SDK surface, unlike the T3 fix's
    four lines against a signature already confirmed real. `samples/README.md`
    and the five sample READMEs underneath it are the map; the top-level
    `README.md`'s bug list and `docs/02-decisions.md`'s samples-structure
    section both point here.

16. **T2 progress narration, fixed for real — and the API it was supposed
    to use turned out not to exist.** Item 15 above left T2's "no useful
    state messages" gap undone, reasoning that fixing it meant "parsing
    custom events out of a Foundry Responses poll loop" against unverified
    SDK surface. Following up on that: downloaded and inspected the real,
    installed `agent-framework-foundry` package (1.10.3) looking for the
    `ctx.emit_custom_event`/`ResponsesHostServer` API
    `05-tier2-hosted-agents.md` §5.1 and §5.4 described — neither exists.
    `agent-framework-foundry` has no `hosting` submodule at all, only
    client/agent-authoring surface. The real T2 container-hosting package
    is a completely different one, `azure-ai-agentserver-responses`
    (`azure.ai.agentserver.responses.hosting.ResponsesAgentServerHost`),
    confirmed by downloading and inspecting it directly — and it has no
    generic "custom application event" concept either, only the standard
    OpenAI Responses event vocabulary (`ResponseStreamEventType`).

    So the `gw.progress.v1`/`emit_custom_event` convention this project's
    docs described as "a decision, not an open experiment" was never
    real anywhere — not built, not buildable against any installed
    package as described. Both `05-tier2-hosted-agents.md` §5.1 (import
    path/class name) and §5.4 (the whole convention) are corrected, along
    with the pin table (`01-gateway-config-and-adapter-contract.md` §3),
    the escalation-table note (`00-tier-model-and-concepts.md` §4), and
    the T3 doc's now-inaccurate "same schema as T2" cross-reference
    (`06-tier3-durable-agents.md` §5.4).

    The actual fix, grounded in a mechanism that does exist and is already
    verified elsewhere in this same investigation: `Response.output` — a
    real field on the `openai` package's `Response` model
    (`AIProjectClient.get_openai_client()` is typed `-> AsyncOpenAI`,
    confirmed in `azure-ai-projects`'s own source, so `_openai` genuinely
    is a standard `openai.AsyncOpenAI` client) — is an ordered list of
    `ResponseOutputItem`s (`function_call`, `mcp_call`,
    `code_interpreter_call`, `reasoning`, `message`, ...) the platform
    attaches to every polled response as the agent works, with real,
    verified field names (`name`, `server_label`, `status`) confirmed
    against the installed `openai` package's own generated types.
    `FoundryResponsesAdapter.follow()` (`src/gateway/upstream/
    foundry_responses.py`) now derives a short narration line from the
    most recent output item on every poll (`_narrate()`) and sets it as
    `StatusEvent.detail` — automatically, for every T2 agent, no
    agent-side opt-in code required, unlike the fabricated convention this
    replaces. `Capabilities.progress` stays `COARSE` deliberately: this is
    best-effort per-*item* narration ("running tool: X"), not a guaranteed
    per-step stream the way T3's explicit webhook push is — `FINE` would
    overclaim. Tests: `tests/test_foundry_progress_narration.py`, item
    shapes verified field-for-field against the installed `openai`
    package, not guessed.

17. **T2 tasks never delivered the agent's actual answer text at all —
    found while building `samples/tier2/02-per-user-isolated-storage`.**
    That sample needs to read a T2 agent's conversational reply back
    (a note count, stated in the model's own words) through the A2A
    surface. Tracing how that would reach a client surfaced that it
    couldn't: item 16's `_narrate()` maps a terminal `message`-type output
    item to the **static string** `"drafting a response"` — appropriate
    while the answer is still being written, wrong once the run is
    actually done. Because `GatewayTaskStoreAdapter.get()`
    (`src/gateway/a2a_server/task_store.py`) also sets `history=[]`
    unconditionally (a separate, previously-documented gap — full
    turn-by-turn history isn't persisted), the *only* place a T2 answer
    could ever have reached a client was `StatusEvent.detail` on the final
    status update. With `_narrate()` returning a placeholder there, no A2A
    client had any path to a T2 agent's actual reply — not a narrower
    progress-fidelity gap like items 15/16, but the delivery of the answer
    itself.

    Fixed with `_detail_for(resp, state)`
    (`src/gateway/upstream/foundry_responses.py`): on a **terminal** state,
    prefer `resp.output_text` — a real `openai` package convenience
    `@property` (verified against the installed package's
    `openai/types/responses/response.py`) that aggregates every
    `output_text` content block from `resp.output` into the same string a
    plain `chat.py`-style caller would print — falling back to
    `_narrate()`'s coarse tool-call narration only if there's no text (a
    failed/canceled run, or a tool-only turn with nothing to say). Non-terminal
    states are untouched: `_narrate()` still drives in-progress narration,
    and a non-empty `output_text` mid-run (partial streamed text) is
    deliberately not surfaced early, so a client never sees a still-forming
    answer reported as if it were final. Tests:
    `tests/test_foundry_progress_narration.py::TestDetailFor` plus a
    `follow()` integration test confirming the real answer text reaches
    `StatusEvent.detail` on completion.

18. **`samples/tier2/02-per-user-isolated-storage` added.** Three
    simulated users (a fake chat UI script issuing real, distinct Entra
    bearer tokens — no dev-mode auth bypass exists in `EntraValidator`, nor
    should one) hit the same hosted agent through the same gateway app,
    interleaved rather than sequential. Two mechanisms demonstrated, kept
    deliberately separate since they're genuinely different subsystems (see
    the sample's README "Two different sandboxes, one story" table):

    - **Per-user `$HOME` isolation**: a function tool
      (`@ai_function`, pre-written Python, executes inside the agent's own
      hosted-session container) appends to a *fixed*-path notes file and
      returns a turn count. Same code, same path, every call — what
      differs, and is what the sample proves, is which sandbox that path
      resolves inside, driven entirely by the gateway's existing
      `identity: per_user` → `x-ms-user-identity` delegation
      (`FoundryHostedAdapter._headers()`, docs/00 §5). No new gateway code.
    - **Artifacts outliving the agent**: code interpreter writes the
      user's prompt into a real `.docx`, using a hand-verified
      `zipfile`-only (no `python-docx`, not installed in the sandbox)
      docx-builder pasted verbatim into the agent's `instructions.md` —
      built and round-tripped through `python-docx.Document()` in this
      sample's own development to confirm Word can actually open it,
      before being trusted in an instructions file the model executes
      unmodified. This reuses the gateway's existing code-interpreter
      harvest pipeline (`_new_artifacts()`, `ArtifactHarvester`) completely
      unchanged, and the download link is read off
      `task.artifacts[].parts[].url` — verified end to end by reading
      `GatewayTaskStoreAdapter._project_artifacts()`
      (`src/gateway/a2a_server/task_store.py`) directly: it mints a fresh
      SAS on every `GetTask` read from `gw_artifact` rows already in
      `state = 'stored'`. No new gateway code here either.

    The one adjacent thing this sample's build *did* change is item 17
    above — without it, the isolation demo's note count would never have
    been visible to the client at all.

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
