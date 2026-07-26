# Tier 3 — Durable Agents

**Stack:** Azure Durable Functions + Microsoft Agent Framework + `a2a-sdk`

⚠ marks items requiring an empirical check. ◆ marks open decisions —
tracked centrally in `08-open-items-and-experiments.md`.

## 1. What tier 3 is — and what it is not

### The reframe

T3 was originally justified on per-step progress. That justification is
gone: T2 can emit progress events from its own code
(`05-tier2-hosted-agents.md` §5.4), and the durable layer's own streaming
is weak — request/response underneath, with streaming only via response
callbacks such as pushing tokens to a Redis Stream, while the entity
returns the complete response after the stream finishes.

**T3's progress story requires a side channel you build, exactly like
T2's, but with more infrastructure around it.** So progress is not a
reason to be here.

### What T3 is actually for

- **Durability across days, weeks or months** — waits that survive host
  restarts without holding compute
- **Deterministic replay** — completed steps are not re-executed after
  failure
- **Genuine multi-day human-in-the-loop** — approvals measured in business
  days
- **Scheduled and event-driven work** — cron, timers, upstream triggers

### What T3 is not for

| Need | Correct tier |
|---|---|
| Per-step progress narration | T2 (§5.4 of tier2 doc) |
| Artifacts / file output | T2, or T1 via code interpreter |
| `input-required` inside one turn | T2 — in-process, no orchestration needed |
| Multi-agent orchestration under an hour | T2 — MAF in-container, no DTS |
| Per-user data access | T1 (⚠ pending T2-FAB-1) |

Some apps currently pointed at T3 belong in T2. Use the escalation table
in `00-tier-model-and-concepts.md` §4, not intuition.

### Cost model

A third model again. T1 bills per token, T2 per concurrent session, T3 per
orchestration and entity operation plus storage transactions.
Checkpointing is chatty, so a high-frequency progress pattern multiplies
transactions.

The redeeming property: **cost scales with steps, not duration.** A
three-week approval wait costs nothing while idle. That is precisely the
workload T3 should be reserved for, and precisely why putting a chatty
sub-minute workflow here is the worst of both worlds.

## 2. Deployment

### 2.1 Project structure

```
research-orchestrator/
├── azure.yaml                  # azd: Function App + DTS + storage + Foundry
├── infra/
│   ├── main.bicep
│   ├── scheduler.bicep         # Durable Task Scheduler + task hub
│   └── functionapp.bicep       # Flex Consumption plan
├── src/
│   ├── function_app.py         # ALL triggers — the platform contract
│   ├── orchestrations/
│   │   ├── research.py         # deterministic orchestrator code
│   │   └── approval.py         # HITL orchestrator
│   ├── activities/
│   │   ├── agents.py           # MAF agent invocations
│   │   ├── artifacts.py        # harvest -> shared blob
│   │   └── notify.py           # progress callback -> gateway
│   ├── a2a/
│   │   ├── server.py           # A2A server surface (agent-framework-a2a)
│   │   └── store.py            # DatabaseTaskStore -> agentsrv schema
│   ├── determinism.py          # guarded clock/random helpers
│   ├── host.json
│   ├── requirements.txt
│   └── local.settings.json     # never committed
├── tests/
│   ├── test_orchestration_replay.py    # replay-safety tests
│   └── test_a2a_contract.py
└── Makefile
```

`function_app.py` is the only file the platform contract touches.
Everything under `orchestrations/` is subject to determinism rules (§5.1).

### 2.2 Packages

```bash
pip install azure-functions azure-functions-durable
pip install agent-framework-azurefunctions --pre     # MAF durable extension
pip install agent-framework-foundry --pre            # FoundryChatClient
pip install agent-framework-a2a --pre                # A2AExecutor
pip install a2a-sdk==<pinned>                        # match gateway's pin
pip install azure-identity azure-storage-blob asyncpg
```

For bring-your-own-compute instead of Functions, swap
`agent-framework-azurefunctions` for `agent-framework-durabletask`.

⚠ Every one of these is prerelease. Verify signatures against the pinned
version — the Learn docs render C# in most tabs and the Python surface
lags.

### 2.3 Local development

```bash
# Durable Task Scheduler emulator — dashboard on :8080
docker run -d --name dts-emulator \
  -p 8080:8080 -p 8082:8082 \
  mcr.microsoft.com/dts/dts-emulator:latest

# Azurite for Functions runtime state
docker run -d --name azurite -p 10000-10002:10000-10002 \
  mcr.microsoft.com/azure-storage/azurite

func start
```

The emulator stores state in local memory and is not for production, but
it gives you the same dashboard experience as the real scheduler —
orchestration traces, entity state, agent conversation history.

### 2.4 Provision and deploy

```bash
azd provision            # Function App, DTS scheduler + task hub, storage, Foundry
azd deploy
func azure functionapp publish <app-name>    # alternative

# post-deploy RBAC — required, easily forgotten
az role assignment create --role "Durable Task Data Contributor" \
  --assignee <function-app-mi> --scope <task-hub-resource-id>
az role assignment create --role "Storage Blob Data Contributor" \
  --assignee <function-app-mi> --scope <artifacts-storage-account-id>
```

The second grant covers both the shared artifact container and, if you
enable large payload support (§6.3), the `durabletask-payloads` container.

### 2.5 ◆ Hosting model — the decision that gates layout

| | Azure Functions (Flex Consumption) | BYO compute (Container Apps / AKS) |
|---|---|---|
| Package | `agent-framework-azurefunctions` | `agent-framework-durabletask` |
| Scaling | to zero, thousands of sessions | you size it |
| Idle cost | none | pay for the floor |
| SSE / long-lived HTTP | constrained | unconstrained |
| Networking | Flex VNet integration | full control |

**Recommendation: Flex Consumption**, because the workload is bursty by
definition — a month-long orchestration is idle almost all of it, and
paying for a warm floor to hold nothing is the wrong trade. Take BYO only
if you need long-lived inbound streaming or networking Flex can't
express.

That recommendation depends on §4.1 below: because the T3 A2A server does
**not** hold SSE connections, the main argument for BYO disappears.

## 3. Identity

T3's identity story is simpler than T2's and more dangerous, for the same
reason: there is no per-session sandbox and no platform-enforced
partition.

### 3.1 The chain

```
Chat UI / Teams bot ──user token──▶ Gateway
                                     │ validate, principal = {tid}.{oid}
                                     │ authorise contextId
                                     ▼
                          A2A message/send + principal in metadata
                                     │
                                     ▼
                          T3 Function App (its own managed identity)
                                     │
                    ┌────────────────┼─────────────────┐
                    ▼                ▼                 ▼
              Foundry models    shared blob      downstream systems
              (app MI)          (app MI)         (app MI)
```

**Everything downstream runs as the Function App's managed identity.**
There is no `x-ms-user-identity` equivalent, no per-user sandbox, no
platform partition.

### 3.2 Consequence: isolation is entirely yours

| | T2 | T3 |
|---|---|---|
| Storage isolation | platform, per-session VM | **your code** |
| Filesystem | private `$HOME` per session | shared app instance |
| Enforcement if you get it wrong | platform still separates sandboxes | none |

So the principal must be carried explicitly and used as a partition key
everywhere:

```python
# activities/artifacts.py
def artifact_key(ctx: TaskContext, name: str) -> str:
    # principal_hash, not principal — blob keys end up in logs and traces.
    return (f"artifacts/{ctx.app}/{ctx.principal_hash}"
            f"/{ctx.context_id}/{ctx.task_id}/{name}")
```

Never derive a partition from anything inside the orchestration input that
a user could influence. Derive it in the gateway, pass it as trusted
context, and treat it as immutable for the life of the orchestration.

### 3.3 Scheduled work has no user

For `initiator = 'schedule'` there is no principal. The schema carries two
separate columns and **they must never substitute for one another**:

```sql
-- gw_context
principal_subject   -- what the work RUNS AS   -> 'system:{schedule_name}'
requested_by        -- who HEARS ABOUT IT      -> the human who scheduled it
reply_channel       -- {transport, conversation_ref}
```

Addressing a result to a user is not authorisation to act as them. Any
code path that reads `requested_by` for an access decision is a bug.

**Linter rule (D6, `L024`):** reject `identity: service` combined with any
`UserEntraToken` connection. That combination is a scheduled job reading
across all users under one identity — the isolation hole in permanent
form.

## 4. Triggers and channels

### 4.1 A2A in — HTTP, not SSE

**Decided:** A2A-to-A2A, with the gateway's `gw_task` as the sole system
of record. `agent-framework-a2a` provides an `A2AExecutor` that adapts a
MAF agent to the A2A server protocol, mapping output to A2A events and
artifacts and managing task status through the official SDK, while your
application supplies the card, request handler, task store, routes and
auth.

The simplification that makes this fit Functions: **the T3 A2A server does
not stream.** It serves `message/send`, `tasks/get` and `tasks/cancel`
over ordinary HTTP, and pushes status *outbound* to the gateway via
webhook.

```
Gateway ──message/send──▶ T3   (returns Task, state=submitted)
Gateway ◀──webhook POST── T3   (status + artifact events, monotonic sequence)
Gateway ──tasks/get─────▶ T3   (reconciliation / catch-up only)
```

Why: durable streaming is a side channel anyway, SSE on Flex Consumption
is constrained, and the gateway already has `LISTEN/NOTIFY` fan-in plus
resumable `sequence` numbers. Pushing beats holding a socket for three
weeks. This is the resolution to what earlier drafts of the gateway spec
listed as "open item #1" (see `08-open-items-and-experiments.md` for the
history) and to D3's gateway↔T3 transport question in `02-decisions.md`.

**Verify this against the installed `a2a-sdk` before writing real code —
it moves fast.** An earlier draft of this snippet imported
`A2AFastAPIApplication` from `a2a.server.apps`; that class does not exist
in `a2a-sdk` 1.1.2 (confirmed by introspecting the installed package while
building the gateway's own A2A surface — see
`01-gateway-config-and-adapter-contract.md` §4 and
`08-open-items-and-experiments.md` item E.2). The actual shape is
protobuf-based route-builder functions mounted onto a FastAPI app directly,
not a single application-builder class:

```python
# a2a/server.py
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from agent_framework.a2a import A2AExecutor
from fastapi import FastAPI

from .store import agentsrv_task_store      # agentsrv.* schema — see §6.3

handler = DefaultRequestHandler(
    agent_executor=A2AExecutor(agent=research_agent),
    task_store=agentsrv_task_store,          # projection, NOT system of record
    agent_card=card,                         # streaming: false — be honest
)

app = FastAPI()
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(card),
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    rest_routes=create_rest_routes(handler),
)
```

This is the same pattern the gateway's own `src/gateway/a2a_server/app.py`
uses for its client-facing surface (which fronts T2/T3, not T3's own
upstream server shown here) — worth reading side by side if this is your
first time in the SDK's route-builder API.

**Declare `streaming: false` on the card.** An upstream card advertising
streaming while the gateway polls makes the gateway's own card lie to
clients.

### 4.2 Cron

```python
# function_app.py
import azure.functions as func
from agent_framework.azurefunctions import AgentFunctionApp

app = AgentFunctionApp(agents=[researcher, writer])   # ⚠ verify signature


@app.timer_trigger(schedule="0 0 6 * * MON",          # 6am Mondays, NCRONTAB
                   arg_name="timer", run_on_startup=False)
@app.durable_client_input(client_name="client")
async def weekly_portfolio_review(timer: func.TimerRequest, client):
    for sub in await load_subscriptions():            # from gw_context
        await client.start_new(
            "portfolio_review",
            instance_id=f"sched-{sub.schedule}-{utc_week_id()}",   # idempotent
            client_input={
                "app": sub.app,
                "principal_subject": f"system:{sub.schedule}",  # runs as
                "requested_by": sub.requested_by,               # tells
                "reply_channel": sub.reply_channel,
            },
        )
```

Note `run_on_startup=False` — leaving it true fires every orchestration on
every deploy and every scale event.

The deterministic `instance_id` makes the trigger idempotent: a duplicate
timer fire lands on the same instance instead of starting a second run.

**Month-long recurrence** uses an eternal orchestration rather than a
timer, so that history doesn't grow without bound:

```python
@app.orchestration_trigger(context_name="context")
def recurring_digest(context):
    cfg = context.get_input()
    yield context.call_activity("run_digest", cfg)

    next_run = context.current_utc_datetime + timedelta(days=30)
    yield context.create_timer(next_run)

    # Resets history. Without this, a year-long eternal orchestration
    # accumulates history until it approaches the state ceiling (§6.3).
    context.continue_as_new(cfg)
```

### 4.3 Teams

**Do not use Foundry's Teams publishing.** T3 isn't a Foundry agent, so
there's nothing to publish — and even for T1/T2 the channel drops user
identity: moving to any Bot Framework channel switches to managed identity
and drops the user token, a limitation tied to the Activity protocol
rather than to Teams. There is also a structural blocker: an Agent
Application supports only one protocol at a time, so an agent published to
Teams cannot also serve Responses to the gateway.

Instead, **your own Teams bot is another gateway client**, peer to the web
UI:

```
Teams ──Activity──▶ your bot ──┬─ Teams SSO → real user token → principal
                               ├─ store conversationReference
                               └──A2A──▶ Gateway ──▶ T3

Gateway push ──▶ bot ──proactive──▶ Teams
```

Your bot doing Teams SSO gets a genuine user token, so `principal.subject`
resolves exactly as from the web UI. The identity loss above is a
property of Foundry's channel wiring, not of Teams.

Constraint: proactive messaging needs a stored `conversationReference`.
You cannot cold-message a user who has never interacted with the app, so a
schedule can only be created by someone who has.

### 4.4 Delegated scheduled execution — don't

Separate **addressing** from **authorising**:

| | Mechanism | Lifetime |
|---|---|---|
| Address the result | `requested_by` + conversation reference | as long as you keep it |
| Authorise the work | delegated token | hours to weeks, then expires |

Refresh tokens are longer-lived — hours to weeks, or until revoked — and
expire after extended non-interaction, requiring the user to consent
again. A monthly job will fail on a horizon you don't control, and the
design requires holding long-lived delegated credentials for every user
who ever scheduled anything.

Preferred patterns, in descending order of sanity:

1. **Split it.** Schedule the shared-scope heavy work; do the per-user
   slice on the user's next interaction, when a live token exists. Teams
   makes this natural: post "your review is ready", enrich on open.
2. **Short-horizon only** — `offline_access` in scopes, re-consent as an
   accepted failure mode, hard cap in days.
3. **Don't.** Snapshotting permissions at schedule time and replaying them
   later is a stale-authorisation bug waiting to happen.

## 5. Patterns

### 5.1 Determinism — read this before writing an orchestrator

Orchestrator code is replayed from history after every await point and
after every failure. It must produce identical decisions on replay.

```python
# ✗ NEVER inside an orchestrator
datetime.utcnow()          random.random()          uuid4()
await http.get(...)        open("/tmp/x")           os.environ[...] (mutable)

# ✓ Deterministic equivalents
context.current_utc_datetime
context.new_guid()
yield context.call_activity("fetch", url)     # all I/O lives in activities
```

Violations produce bugs that appear **only after a failure and replay** —
the hardest class to reproduce, and invisible in a happy-path test.

Mitigations, all three:

1. `determinism.py` exposes guarded helpers; orchestrators import nothing
   else.
2. A lint rule bans the forbidden symbols under `orchestrations/`.
3. `tests/test_orchestration_replay.py` runs each orchestration twice from
   history and asserts identical action sequences.

### 5.2 Multi-agent orchestration with checkpointing

```python
# orchestrations/research.py
@app.orchestration_trigger(context_name="context")
def research(context):
    cfg = context.get_input()

    researcher = app.get_agent("ResearchAgent")   # ⚠ verify Python signature
    writer     = app.get_agent("WriterAgent")

    yield context.call_activity("notify", _p(cfg, "research_started"))

    findings = yield researcher.run(f"Research: {cfg['topic']}", context=context)
    yield context.call_activity("notify", _p(cfg, "research_done"))

    draft = yield writer.run(
        f"Write about {cfg['topic']}. Findings: {findings.text}", context=context)

    uri = yield context.call_activity("harvest_artifact",
                                      {"cfg": cfg, "body": draft.text})
    yield context.call_activity("notify", _p(cfg, "completed", artifact=uri))
    return uri
```

Each agent call is checkpointed and completed calls are not re-executed on
recovery, so a crash between the research and writer steps resumes
without paying for the research again. That property — not progress — is
why this code is here rather than in T2.

### 5.3 Multi-day human-in-the-loop

```python
# orchestrations/approval.py
@app.orchestration_trigger(context_name="context")
def expense_approval(context):
    cfg = context.get_input()

    yield context.call_activity("request_approval", cfg)   # posts to Teams

    approval = context.wait_for_external_event("APPROVAL")
    deadline = context.create_timer(
        context.current_utc_datetime + timedelta(days=14))

    winner = yield context.task_any([approval, deadline])

    if winner == approval:
        deadline.cancel()                    # ALWAYS cancel the loser
        return (yield context.call_activity("reimburse", approval.result))

    yield context.call_activity("notify_timeout", cfg)
    return {"status": "expired"}
```

Two rules: **always cancel the losing timer** — an uncancelled durable
timer keeps the instance alive — and give every wait a deadline. An
orchestration waiting on an event that will never arrive waits forever,
costs nothing, and is invisible until someone audits instance counts.

The gateway maps this to A2A `input-required`, and the reply arrives as
`client.raise_event(instance_id, "APPROVAL", payload)`.

**The reaper must not touch it.** `gw_event`'s index covers `state IN
('submitted','working')`, which correctly excludes `input-required` — but
the upstream lease needs the same carve-out. ◆ A 45-day approval outliving
its 30-day context retention (D5) needs a stated policy.

### 5.4 Progress — a push activity, not a stream

```python
# activities/notify.py
@app.activity_trigger(input_name="payload")
async def notify(payload: dict) -> None:
    """POST a status event to the gateway callback.

    An activity, so it's checkpointed and retried by the platform, and so the
    orchestrator stays free of I/O. `sequence` is assigned by the orchestrator
    from a replay-safe counter, giving the gateway ordering and dedupe.
    """
    await http.post(
        f"{GATEWAY_CALLBACK}/tasks/{payload['task_id']}/events",
        json=payload,
        headers={"Authorization": f"Bearer {await callback_token()}"},
    )
```

Same `gw.progress.v1` schema as T2 (`05-tier2-hosted-agents.md` §5.4). One
event vocabulary across all three tiers, one client rendering path.

### 5.5 Artifacts

Identical contract to T1 and T2: harvest into the shared blob container,
index in `gw_artifact`, serve via short-lived user delegation SAS. Full
policy in `07-artifacts-and-code-interpreter.md`.

```python
@app.activity_trigger(input_name="payload")
async def harvest_artifact(payload: dict) -> str:
    cfg = payload["cfg"]
    key = artifact_key(cfg, "report.md")
    await blob.upload(key, payload["body"].encode(), overwrite=False)  # idempotent
    return key
```

Never return artifact bytes from an activity into orchestration state —
return the key. This is what keeps the state-size ceiling (§6.3)
irrelevant for outputs.

For `initiator = 'schedule'` the prefix takes the system branch:

```
artifacts/{app}/system/{schedule_name}/{run_id}/{artifact_id}-{name}
```

## 6. The three planes

### 6.1 Architecture

| Item | State |
|---|---|
| A2A-to-A2A, gateway is system of record | ✓ decided (§4.1) |
| No SSE upstream; webhook push instead | ✓ decided (§4.1) |
| Hosting model — Flex vs BYO | ◆ recommended Flex (§2.5) |
| Escalation table rewrite | ◆ done — see `00-tier-model-and-concepts.md` §4 |
| `affinity: context` for T3 | ✓ **removed** — see below |

**Affinity was wrong in earlier drafts.** An earlier config example
specified instance pinning for T3, but with DTS the state lives in the
scheduler and any worker resumes any orchestration — that is the entire
point of the backend. Pinning defeats the scaling model. Affinity is only
relevant for T3 on BYO compute *without* DTS. It has been removed from the
config schema in `01-gateway-config-and-adapter-contract.md` §1.

**Cancellation semantics.** ◆ A2A `cancel` and durable `terminate` are not
the same operation. Terminate is abrupt; work killed mid-activity can
leave external side effects with no compensation. Document the contract:
does cancel mean "stop scheduling new steps" (clean, slower) or "kill now"
(fast, possibly inconsistent)? Pick one per app and put it on the card.

### 6.2 Control plane

| Item | State |
|---|---|
| DTS RBAC grants in pipeline | ◆ open |
| Instance ID scheme + purge policy | ◆ open |
| Orchestration versioning | ◆ open — the sharp one |
| Three-way A2A version matrix | ⚠ verify |
| Python surface parity | ⚠ verify |

**Orchestration versioning is sharper than T2's cutover problem.** A
running orchestration replays against *deployed* code. Change the
orchestrator's shape while instances are in flight and replay diverges
from history — a non-deterministic failure on live work. For month-long
instances, some will always be in flight. Options: version the
orchestrator name (`research_v2`) and let old instances drain against old
code, or gate changes behind `context.get_input()["version"]`.
Name-versioning is the safer default and needs to be a convention before
the first change lands, not after.

**Purge policy.** Autopurge is configurable on DTS; completed instances
otherwise accumulate. Align retention with `gw_task` (D5: 90 days) so the
dashboard and the gateway tell the same story.

**Version matrix.** ⚠ Gateway's A2A target, `agent-framework-a2a`'s
`a2a-sdk` pin, and Foundry's 1.0/0.3 support must negotiate. Test it;
don't assume.

### 6.3 Data plane

| Item | State |
|---|---|
| Shared Postgres server, separate schema | ✓ decided — see `03-postgres-schema.md` |
| Shared blob container | ✓ decided — see `07-artifacts-and-code-interpreter.md` |
| State size ceiling | ✓ mitigated by design; ⚠ one gap |
| Session TTL vs D5 retention | ◆ conflict |
| Trace correlation across three panes | ⚠ close early |

**State size ceiling.** The 1 MB figure is a Durable Task Scheduler
boundary — not Azure Tables (DTS manages state internally with no separate
storage account) and not an SDK limit. The Azure Storage backend is the
one that uses queues, tables and blobs, and it has different
characteristics.

Large payload support offloads to Blob Storage for the .NET and Python
SDKs:

```bash
pip install durabletask[azure-blob-payloads] durabletask-azuremanaged
```

Keep `ThresholdBytes` at or below 1,048,576; the reference sample uses
900,000 so payloads offload before reaching the 1 MiB scheduler message
boundary. Offloaded payloads land in a `durabletask-payloads` container,
and the app identity needs **Durable Task Data Contributor** on the task
hub plus **Storage Blob Data Contributor** on the storage account.

Two caveats: it is **preview**, so a `preview: deny` app (D10) can't use
it; and ⚠ the large-payload docs describe orchestration **inputs and
outputs**, while the 1 MB figure raised above was **entity state**, and
durable agent sessions are built on entities. Whether offload covers
entity state is unconfirmed — test it, since that's the case that
actually bites.

Either way the documented best practice is the better design and is what
§5.5 already does: keep large data in external storage and materialise it
only inside activities. Combined with `continue_as_new` (§4.2), you stay
clear of the boundary rather than relying on a preview feature to rescue
you.

**TTL conflict.** ◆ Default durable session TTL is 14 days and TTL
configuration is currently .NET-only. We're Python. So T3 sessions expire
at 14 days while D5 promises 30-day sliding conversation retention — a
user returning on day 20 gets history with no session behind it. Either
D5 gets a per-tier override, or T3 apps need a keep-alive, or the UI
states the shorter horizon. **Decide before the first month-long app
ships** — this is one of the sharper unresolved conflicts in the whole
design (see `08-open-items-and-experiments.md`).

**Trace correlation.** ⚠ Harder than T2: gateway → A2A → orchestration →
activity, spread across the DTS dashboard, App Insights and your own
traces. Three panes, one request. Propagate W3C `traceparent` from the
gateway through the A2A call and into activity inputs, and confirm it
survives replay. Close this early — everything else is harder to debug
without it.

## 7. Checklists

### Before first deploy

- [ ] Hosting model chosen and recorded (§2.5)
- [ ] DTS scheduler + task hub provisioned; emulator working locally
- [ ] `Durable Task Data Contributor` on the task hub for the app identity
- [ ] `Storage Blob Data Contributor` on the artifacts storage account
- [ ] `agentsrv` schema created; separate from `gateway`
- [ ] All prerelease packages pinned; signatures verified against those pins
- [ ] Agent card declares `streaming: false`

### Before writing an orchestrator

- [ ] `determinism.py` in place; lint rule bans forbidden symbols
- [ ] Replay test harness runs each orchestration twice and diffs actions
- [ ] Orchestrator-name versioning convention agreed **before** the first change
- [ ] Every wait has a deadline; every race cancels the loser

### Before shipping a scheduled app

- [ ] `run_on_startup=False` on every timer trigger
- [ ] Deterministic `instance_id` so duplicate fires are idempotent
- [ ] `principal_subject = 'system:{schedule}'`, `requested_by` separate
- [ ] `reply_to` present — nobody is holding a stream
- [ ] Linter `L024` passes: no `identity: service` with `UserEntraToken`
- [ ] Eternal orchestrations call `continue_as_new`

### Before shipping a multi-day HITL app

- [ ] Reaper carve-out verified for `input-required`
- [ ] Retention policy states what happens when an approval outlives its context
- [ ] Timeout path tested, not just the approval path
- [ ] Teams `conversationReference` stored and proactive delivery tested
