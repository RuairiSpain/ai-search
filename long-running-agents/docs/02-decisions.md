# Finalised Decisions (D1–D10)

**Status:** decisions accepted; items marked ⚠ require an empirical check
before build — tracked centrally in `08-open-items-and-experiments.md`.

**Scope note:** these decisions were made across all three tiers, and most
still apply that way — D1's isolation model, D7's steering/cancellation
semantics, and D10's preview gating are relevant to a T1 agent wherever it's
actually fronted. But T1 is not a gateway tier (`00-tier-model-and-concepts.md`),
so where a decision below is specifically about *this gateway's* runtime
behavior, only its T2/T3 half is something the code in `src/gateway/`
actually implements — a T1 mention is design intent for T1's own front door
(Foundry's native A2A endpoint), not something to look for in this codebase.

---

## D1 — Conversation isolation

**Decision:** opaque gateway-owned mapping, plus a metadata stamp, plus
`x-ms-user-identity` on every Responses call. Three layers, not one.

`x-ms-user-identity` is **not hosted-agent-only.** It is documented on the
*Create a model response* REST API as an optional opaque per-user identity
string that scopes endpoint-scoped data to a specific end user, requiring the
`agents/endpoints/UserIdentityImpersonation/action` RBAC permission. That
means the same delegation header applies to T1 — send it on every call.
⚠ "Endpoint-scoped data" is not defined precisely enough to assume it covers
conversations — verify with test **ISO-1** before relying on it.

### Where the conversation ID comes from

The gateway creates it. **The client never sees a Foundry conversation ID
and never supplies one.**

```
chat client  --A2A contextId-->  gateway  --conversation_id-->  Foundry
             (opaque, random)             (never leaves gateway)
```

The A2A `contextId` is an opaque random identifier the gateway issues. On
every inbound request carrying a `contextId`, the gateway **authorises it
against the caller's principal** before resolving it to a conversation. This
is the single most important control in the system: a client-supplied
`contextId` that is not authorised is a direct IDOR vulnerability.

**Rejected: deriving IDs from the user ID** (`{user_id}-{suffix}`).
Derivable identifiers are strictly worse than random ones — knowing a user
ID lets an attacker enumerate context IDs — and they add no security,
because the check that matters is the authorisation lookup, not the shape
of the string. Identifiers are for lookup; authorisation is for security.
Never conflate them.

Many conversations per user is already supported: one `gw_context` row per
conversation, all sharing a `principal_subject`.

**Implementation note (a2a-sdk integration):** `a2a-sdk` resolves
`contextId` before the gateway's own code runs — either taken from the
client's message, or minted by the SDK's own generator when the client
omits one. The gateway no longer gets to unilaterally choose the id the way
the sentence above describes literally. `ContextStore.get_or_create_context`
implements the practical equivalent instead: an atomic INSERT-or-authorise
against `gw_context` (`ON CONFLICT DO NOTHING RETURNING *`, falling back to
`authorise_context` if the row already exists). This loosens D1's letter
("the gateway creates it") while preserving the property that actually
matters — ownership stays principal-scoped and atomic, so a client can never
claim a `contextId` that belongs to someone else, satisfied whether the
gateway or the client happened to pick the string.

### Layer 3: metadata stamp

Conversations accept metadata at creation. Stamp a salted hash of the
principal:

```python
conv = await openai.conversations.create(
    metadata={"gw_principal": hmac_sha256(principal.subject, PEPPER)[:32],
              "gw_app": app},
)
```

On every resume, re-read the conversation and compare. Mismatch → refuse
and alert. This catches gateway mapping bugs that the unique index would
not, and costs one cheap call.

### Also set on every request

- `prompt_cache_key` — per-user bucketing, improves cache hit rates
- `safety_identifier` — stable per-user ID for abuse detection

Both replace the deprecated `user` field. Use the same value as
`x-ms-user-identity`.

### ⚠ Test ISO-1 (run before writing adapter code)

1. As the gateway identity, create conversation A with
   `x-ms-user-identity: alice`.
2. As the gateway identity, attempt `conversations.retrieve(A)` with
   `x-ms-user-identity: bob`.
3. Repeat with the header omitted entirely.

If either succeeds, the platform does **not** enforce conversation scoping
and `gw_context` is the entire security boundary. Record the result here;
it determines the review weight on the mapping code.

---

## D2 — Memory scoping

**Decision:** platform *session* memory only. User and procedural memory
live in the gateway's Postgres, keyed by `principal_subject`, until
**ISO-2** proves platform scoping.

**Rejected: a per-request key round-tripped through the client.** The
proposal was for the gateway to mint a key, hand it to the UI, and have the
UI return it to unlock memory. Rejected because:

- A key the client holds and returns **is a bearer token**. It grants
  access on presentation — a second credential to issue, rotate, revoke and
  leak, with no benefit over the token the user already presents.
- It moves an authorisation decision to the client. A tampered or replayed
  key is indistinguishable from a legitimate one.
- Rotation per request breaks legitimate multi-tab/reconnect flows, and any
  scheme lenient enough to fix that reintroduces replay.

**The user's verified token is already the key.** Derive everything
server-side from it and never round-trip a capability through the browser.

### Why gateway-side memory for now

Memory is preview, and if user memory scopes to the *calling* identity,
every chat user shares one store. Unlike a conversation mix-up — which a
user would notice — memory contamination is silent, persistent, and would
poison responses for months before anyone traced it. The blast radius
justifies the caution.

Gateway-side memory also gives you something the platform doesn't: the
ability to show, edit and delete a user's memories, needed for
data-subject requests regardless.

Session memory is safe to use now: it is scoped to a conversation, and
conversation scoping is already the boundary D1 enforces.

### ⚠ Test ISO-2

Write a user memory as `alice`, read as `bob`, with and without
`x-ms-user-identity`. If isolated, promote user memory to the platform and
delete the gateway implementation.

### Note on OBO

Foundry supports on-behalf-of authentication **for tools** — tools can call
downstream services as the signed-in user. This is *tool-level* OBO and
does not by itself scope conversations or memory. Do not assume it solves
D1 or D2. It is, separately, the right answer for Fabric and SharePoint
grounding, where row-level security must follow the end user (see
`05-tier2-hosted-agents.md` §4).

---

## D3 — Streaming transport

**Decision: SSE everywhere the gateway itself streams. No WebSockets
anywhere.**

| Hop | Transport | Notes |
|---|---|---|
| chat client ↔ gateway | **SSE** | A2A's own `message/stream` is SSE |
| gateway ↔ Foundry (T1/T2) | **SSE** | Responses API `background=True, stream=True` |
| gateway ↔ T3 | **webhook push**, not SSE | decided in tier3 doc §4.1 — see correction below |

SSE is plain HTTP, unidirectional, proxy-friendly and resumable.
WebSockets would add bidirectional state you don't need, since the
client's upstream messages are ordinary A2A POSTs.

**Correction applied during merge:** an earlier draft of this decision left
the gateway↔T3 hop as "SSE or callback, adapter's choice." The T3 guide
later resolved it firmly to **webhook push only** — the T3 A2A server does
not stream at all (Flex Consumption's SSE support is constrained, and
durable streaming is a side channel either way). See tier3 doc §4.1 for the
full reasoning; the config schema's `streaming: false` card flag reflects
this.

**Scaling.** The cost is one held connection per *active* task, not per
user. Mitigations already in the design: `LISTEN/NOTIFY` lets any replica
serve any stream, and the `sequence` column makes reconnection resumable
rather than restarting. Add: close the stream on terminal state, and fall
back to polling for tasks idle beyond a threshold. A T3 task running for
six hours should not hold a socket for six hours (moot now that T3 doesn't
stream at all, but the same principle applies to a long T1/T2 poll).

Per Foundry's REST guidance: treat `[DONE]` as the terminal marker, close
cleanly, and reconnect with exponential backoff resuming from application
state. Our application state is `(task_id, last_sequence)`.

**Upstream default:** prefer `background=True, stream=True` where the
model supports it; poll otherwise. Per-upstream capability flag, resolved
by the linter (D6), not hand-configured.

---

## D4 — `input-required` convention

**Decision:** structured output contract, enforced by the linter.

T1 agents that can ask clarifying questions must declare:

```yaml
outputSchema:
  properties:
    status:   { type: string, enum: [answered, needs_input], required: true }
    message:  { type: string, required: true }
    question: { type: string, required: false }
```

The adapter maps `status: needs_input` → `TaskState.INPUT_REQUIRED` and
emits `question` as the message. Without this, a prompt agent asking a
question is indistinguishable from one answering, and the UI cannot render
a reply affordance.

The linter rejects any app with `input_required: true` whose agent lacks a
conforming `outputSchema` (`L013`).

---

## D5 — Retention

**Decision:** sliding 30-day conversation retention by default, per-app
override, with 24h available as a data-minimisation mode.

24 hours is a legitimate *policy* choice, but recognise the cost: users
expect to reopen yesterday's chat, and a hard 24h expiry means every
returning user starts cold. Recommend 24h only for apps handling sensitive
data where minimisation outweighs continuity, and make it explicit in the
app's card so the UI can warn.

| Object | Default | Notes |
|---|---|---|
| `gw_context` + Foundry conversation | 30d sliding | resets on activity |
| `gw_task` / `gw_event` | 90d | needed for support and audit |
| blob artifacts | 90d, then cool, delete at 365d | per-app prefix policy |
| agent versions | see D8 | **not** tied to conversation retention |

One deletion path must remove all four for a given principal. Build it now;
retrofitting a GDPR erasure path across four stores later is miserable.

**Known conflict (T3):** default durable session TTL is 14 days and TTL
configuration is currently .NET-only (we're Python). T3 sessions can
therefore expire before the 30-day conversation-retention promise does — a
user returning on day 20 gets history with no session behind it. Tracked as
an open item; resolve before the first month-long T3 app ships (see
tier3 doc §6.3 and `08-open-items-and-experiments.md`).

---

## D6 — Background mode and the linter

### Background mode

Documented as supported on frontier reasoning models; models without it
fall back to synchronous execution under a **100-second timeout**.

**Decision: do not hardcode a model list.** Any list rots within a quarter
and model availability varies by region. The linter queries the target
project's deployments at build time and resolves the capability from the
live service. A static list belongs only in documentation as illustration.

### Linter rule catalogue

`gwlint` runs in CI against `azure.yaml`, `agents/*.yaml`, `workflows/*.yaml`
and the gateway's `apps.yaml`.

**Resolution**
- `L001` model deployment exists in the target project
- `L002` model available in the project's region
- `L003` `instructionsFile` resolves and is non-empty
- `L004` every `skills`, `toolboxes`, `connections` reference resolves
- `L005` `foundry_agent` in gateway config matches a declared agent

**Capability**
- `L010` `default_mode: long` requires a background-capable model
- `L011` tool available in the project's region and on the chosen model
- `L012` code interpreter apps declare `container_policy`
- `L013` `input_required: true` requires the D4 `outputSchema`
- `L014` T2 apps: gateway identity holds `UserIdentityImpersonation`

**Safety**
- `L020` `identity: service` requires a `justification` field
- `L021` `x-ms-user-identity` source charset is valid (`[A-Za-z0-9._:@-]`, 1–256)
- `L022` no secrets inline; `${VAR}` only
- `L023` push-notification URLs are on the SSRF allowlist
- `L024` reject `identity: service` combined with any `UserEntraToken`
  connection (tier3 doc §3.3) — a scheduled job reading across all users
  under one identity, the isolation hole in permanent form

**Preview** (see D10)
- `L030` preview feature used while `preview: deny`
- `L031` container protocol < 2.0.0
- `L032` any Assistants API usage — hard fail

Severity: `L0xx` safety rules fail the build. Capability rules fail unless
explicitly waived with an expiry date.

---

## D7 — Cancellation and mid-run steering

### Cancellation

The Responses API defines `cancelled` as a response status, so cancellation
exists as a concept. ⚠ Verify on your API version: the endpoint shape,
whether it stops billing, and what it does to an attached code interpreter
container. Until verified, `Capabilities.cancel` for T1 stays behind a
feature flag and `tasks/cancel` returns `canceled` only after the upstream
confirms — never optimistically.

**Implementation note (a2a-sdk integration):** "never optimistically" does
not mean "let the original follow loop notice it." `a2a-sdk`'s
`ActiveTask.cancel()` force-cancels the task's running `AgentExecutor.execute()`
coroutine *before* awaiting `AgentExecutor.cancel()` — so the loop that
would normally observe the upstream's status transition and relay it is
already gone by the time `cancel()` runs. The confirmation this decision
requires is `adapter.cancel()` returning successfully; `GatewayAgentExecutor.cancel()`
persists the terminal state directly against the store right after that
call, rather than through the event queue the executor also owns (which the
producer's own teardown may be concurrently closing — an event enqueued
there is silently dropped, not merely delayed).

### Mid-run steering — supported, tier-dependent, cooperative

**The universal constraint:** steering is always *cooperative* and
*checkpoint-granular*. Nothing interrupts a model mid-generation. The only
variable is how fine the checkpoints are, and that is a property of the
upstream — the gateway cannot improve it, only report it honestly.

#### Capability by tier

| Tier | Steering | Mechanism | Latency to effect |
|---|---|---|---|
| T1 single response | `none` | — | next turn only |
| T1 workflow | `checkpoint` ⚠ | `SetVariable` re-read before each agent node | next node |
| T2 hosted | `checkpoint` | interjection file via Session Files API, agent polls between steps | next step |
| T3 durable | `checkpoint` | `wait_for_external_event` raced with the agent step via `task_any` | next orchestration step |

**T1 single response.** A background response executes against fixed
input. `conversations.items.create()` during a run appends to the
conversation store, but the running response never reads it — the item
surfaces on the *next* turn. So T1 offers **deferred** steering: queue it,
apply it next turn. The only real-time alternative is cancel-and-reask with
amended input, which discards partial work and depends on unverified
cancel semantics above.

**T1 workflows.** ⚠ Inferred from the workflow YAML shape, not documented
— spike before promising it. `InvokeAzureAgent` takes `input: messages:`
from a local variable and does not re-read the conversation, so appended
items are invisible by default. An author can add a `SetVariable` before
each agent node that re-reads recent conversation items and folds in any
pending interjection. That yields cooperative steering at author-chosen
checkpoints from documented primitives. It is opt-in per workflow, so
`steering` is a property of the *workflow*, not of tier 1 as a whole.

**T2.** Your loop, your rules. Gateway writes an interjection file through
the Session Files API; the agent checks for it between steps. Documented
primitives, entirely your implementation.

**T3.** Native. `wait_for_external_event` gives a running instance a
re-entry point; racing it against the agent step with `task_any` gives
non-blocking steering rather than a pause. Checkpoints are every
orchestration step.

#### Required code additions

1. **`gw_interjection` table.** Interjections are events, not messages, and
   must not pollute `gw_event`. Schema in `03-postgres-schema.md`.
2. **`UpstreamAdapter.steer()`** returning `Accepted | Queued | Unsupported`.
   Distinct from `resume()`, which replies to a *paused* task. Sending to a
   `working` task is a different operation and must not reuse that path.
3. **`Capabilities.steering: none | deferred | checkpoint`**, declared per
   upstream and surfaced on the agent card — same discipline as progress
   fidelity.

#### ⚠ A2A protocol question

Verify whether the targeted A2A version permits `message/send` against a
task in `working` state, or only in `input-required`. If it disallows it,
interjections need a gateway-local endpoint rather than the standard
message path, and the agent card must advertise it as an extension.

#### The product risk

If steering is `deferred` or `none`, **the UI must say so.** A user who
types a correction and watches the agent continue regardless concludes the
system is broken — worse than not offering the feature. Minimum: "Queued —
applies at the next step." For T1 single-response apps, disable the input
box while a task is `working` rather than accepting text you cannot act on.

#### Security envelope — applies at every tier

An early proposal was to prefix injected text with `IMPORTANT ***`.
**The urgency marker is the problem, not the feature.** A channel
delivering end-user text to a downstream agent flagged as high-priority is
a prompt-injection channel built on purpose — anyone who influences what a
user pastes can override the agent's instructions.

Non-negotiable:

1. Inject as **user-role content only**. Never system, never instructions.
2. **No urgency markers.** Use a neutral, attributed envelope:
   `<user_interjection>…</user_interjection>`. Let the receiving agent's
   own instructions decide the weight.
3. Receiving agents state explicitly that interjections are *advisory* and
   cannot change their task, tools or output contract.
4. Cap length; strip control characters; rate-limit per task.
5. Tool permissions and output schema are frozen at task start and
   **cannot** be altered by an interjection.
6. Log every interjection against the task for audit.

Design it as "the user added context", never "the user issued an
override".

---

## D8 — Agent version retention

**Decision: do not tie version retention to conversation retention.**

The reasoning "conversations last 30 days, so versions older than that are
safe to delete" doesn't hold:

- **Attribution.** Artifacts live 90–365 days (D5). An artifact whose
  producing version was deleted is unattributable, defeating the audit
  trail.
- **Rollback.** Version pinning is the rollback mechanism: switch the
  pointer back to the previous version, no redeploy. Deleting old versions
  deletes the rollback.
- **Evaluation.** Regression testing compares a candidate against a
  baseline. No baseline, no gate.
- **In-flight tasks.** A T3 orchestration can outlive 30 days and still
  reference its version.

**Policy:** keep the union of —

- the last **10** versions per agent, and
- every version referenced by a non-terminal `gw_task`, and
- every version referenced by a non-expired `gw_artifact`, and
- every version tagged `released` in the last **180 days**.

Prune anything else weekly. The real fix for version sprawl is upstream:
content-hash the definition in CI and skip `create_version()` when
unchanged, so a no-op deploy doesn't mint a version at all.

---

## D9 — Region and availability validation

Folded into D6 as `L002` and `L011`. Tool availability varies by region
*and* by model — both dimensions are checked, and both are resolved live
from the target project rather than from a table in the repo.

The linter runs per environment. An agent that passes in `dev` (West
Europe) can legitimately fail in `prod` (Sweden Central), and that must be
a build failure in the prod pipeline, not a runtime surprise.

---

## D10 — Preview versus GA

**Decision:** every app declares a stability floor; the linter enforces it.

```yaml
apps:
  - name: payments-triage
    preview: deny          # allow | deny  (default: deny)
```

`deny` restricts the app to the GA-only subset:

**GA and usable under `deny`**
prompt agents · Responses & Conversations · code interpreter · MCP ·
file search · web search · Azure AI Search · function calling · OpenAPI ·
Grounding with Bing

**Preview — blocked under `deny`**
memory · workflows · routines · toolboxes · A2A (incoming and tool) ·
hosted agents · Fabric IQ · SharePoint grounding · browser automation ·
computer use · image generation · azd prompt-agent deployment · durable
large-payload offload (tier3 doc §6.3)

**Retired — blocked always**
Assistants API / threads / runs / messages (retires 26 Aug 2026) ·
container protocol 1.0.0 (blocked 31 Jul 2026)

Note the consequence: **`preview: deny` means tier 1 only, single agent, no
memory and no workflows.** That is a real constraint and teams should meet
it knowingly. Document it as the "regulated app" profile.

---

## Documentation and samples structure (target, for once the codebase exists)

```
docs/
  README.md                     # what this is, 5-minute path to a running agent
  concepts/
    tiers.md
    identity-and-isolation.md
    lifecycle.md
    preview-vs-ga.md
  authoring/
    tier1-agent.md
    tier1-workflow.md
    instructions-style.md
    output-schemas.md
    onboarding-checklist.md
  operations/
    linter.md
    runbook-stuck-task.md
    runbook-artifact-loss.md
    runbook-isolation-alert.md
  reference/
    gateway-config.md
    a2a-surface.md
    postgres-schema.md
samples/
  tier1/  01-basic  02-mcp-tool  03-code-interpreter  04-artifact-harvest
          05-input-required  06-session-memory  07-workflow-sequential
          08-workflow-executor-reviewer  09-workflow-hitl  10-structured-output
  tier2/  01-hosted-basic  02-per-user-isolation  03-session-files  04-long-running
  tier3/  01-durable-agent  02-multi-agent-orchestration  03-hitl-durable  04-a2a-server
  gateway/  01-local-dev  02-end-to-end
```

### Requirements every sample must meet

1. Runs from a clean clone with `make run` and documented env vars only.
2. Declares its tier and stability in a header block: tier, preview or GA,
   required RBAC roles, required region features.
3. Shows the failure, not just the happy path — at minimum one deliberate
   error and its correct handling.
4. Passes `gwlint` with zero waivers. A sample that needs a waiver is a
   documentation bug.
5. Includes teardown. `make down` removes agents, conversations, uploaded
   files and blobs.
6. Pinned versions referencing the single pin table (`01-gateway-config-and-adapter-contract.md`
   §3), never inline version strings.
7. No secrets, no account keys. `DefaultAzureCredential` and `${VAR}` only.

### Requirements for docs pages

- Lead with the decision or the answer; rationale second.
- Every claim about platform behaviour carries a **verified-on date** and
  the API version it was verified against.
- Anything preview is badged inline, not only in a footer.
- Every runbook states its trigger (which alert fires) and its escalation
  path.
- Concepts pages link to a sample; samples link back to the concept.
