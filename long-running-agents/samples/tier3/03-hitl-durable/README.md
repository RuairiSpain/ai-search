# T3 sample 03 — multi-day human-in-the-loop (durable)

| | |
|---|---|
| Tier | **T3** (durable orchestration), fronted by this gateway |
| Stability | **Preview**, entirely: `agent-framework-durabletask`/`-azurefunctions`/`-a2a` are all pinned `--pre` (docs/01 §3) |
| RBAC | Function App managed identity needs `Durable Task Data Contributor` on the task hub and `Storage Blob Data Contributor` on the artifacts container (docs/06 §2.4) |
| Region features | Durable Task Scheduler + Flex Consumption availability in-region |

## What this shows

`resume()`/`input_required` on T3, end to end, using the exact pattern
`docs/06-tier3-durable-agents.md` §5.3 documents but never turns into
something runnable: an expense-approval orchestration that pauses on
`context.wait_for_external_event("APPROVAL")`, racing it against a
deadline timer via `context.task_any`. The gateway sees the pause as
`TASK_STATE_INPUT_REQUIRED` — nothing new on the gateway side had to be
built for T3 to make this work (unlike T2, where `resume()`/`input_required`
detection needed real gateway code — see `docs/08-open-items-and-experiments.md`
item 20 — T3's `DurableAdapter` already had `capabilities.input_required =
True` hardcoded and `_SDK_STATE_TO_GW` already mapped
`TASK_STATE_INPUT_REQUIRED` directly). What this sample actually had to
build is the missing other half: **this app's own A2A server** has to know
what a second `SendMessage` against a paused task *means* — see "How resume
actually works" below.

## What you'll actually see

```
$ python client/approve.py "Client dinner, $85"
[00:00] TASK_STATE_SUBMITTED  (task task_a1b2c3d4)
[00:03] TASK_STATE_WORKING
[00:06] TASK_STATE_INPUT_REQUIRED  "Waiting for approval: Client dinner, $85"

Paused for approval. Answer with:
  python client/approve.py task_a1b2c3d4 --decision approved
  python client/approve.py task_a1b2c3d4 --decision rejected --reason "..."
```

```
$ python client/approve.py task_a1b2c3d4 --decision approved
[00:00] TASK_STATE_WORKING
[00:03] TASK_STATE_COMPLETED  "Reimbursed 'Client dinner, $85' (approved by alice)."

final state: TASK_STATE_COMPLETED
```

## How resume actually works

Three moving parts, in the order a request actually flows through them:

1. **The gateway routes the reply.** `GatewayAgentExecutor._continue_existing()`
   (`src/gateway/a2a_server/executor.py`) checks the task's state; for
   `TASK_STATE_INPUT_REQUIRED` it calls `adapter.resume(...)`.
2. **`DurableAdapter.resume()`** (`src/gateway/upstream/durable.py`) does
   nothing T3-specific at all — it just re-calls `submit()`, i.e. another
   `SendMessage`, against this sample's own A2A server, with the *same*
   `contextId` the paused task already has.
3. **This sample's `a2a/server.py`** is what has to notice that's a resume,
   not a new request, and do something different with it:
   `ApprovalExecutor._continue_existing()` unconditionally calls
   `client.raise_event(instance_id, "APPROVAL", payload)` — deliberately
   **not** gated on this server's own local task state, because that local
   `InMemoryTaskStore` is a `tasks/get` reconciliation projection, not the
   system of record (`gw_task` is, docs/06 §6.3), and it's never told about
   the orchestration's own "now waiting" moment — see the docstring in
   `src/a2a/server.py` for the full reasoning. In this sample's flow, the
   only reason a second message ever arrives against an existing task_id
   is a human's decision, so there's nothing else it could mean.

The gateway learns about the pause itself the same way it learns about
every other T3 status change: the orchestration's `notify` activity pushes
`{"state": "input-required", ...}` to the gateway's webhook, **before**
the orchestration actually starts waiting (`orchestrations/approval.py`) —
so a client watching (by polling, or subscribed via
`../05-push-notifications`'s mechanism) sees the pause happen, not just
its eventual resolution.

## The deliberate failure path: the timeout, not just the approval

`docs/06` §7's own "Before shipping a multi-day HITL app" checklist lists
*"Timeout path tested, not just the approval path"* — this sample takes
that literally:

```
$ python client/approve.py "Client dinner, $85" --timeout-seconds 20
[00:00] TASK_STATE_SUBMITTED  (task task_...)
[00:03] TASK_STATE_WORKING
[00:06] TASK_STATE_INPUT_REQUIRED  "Waiting for approval: Client dinner, $85"
(this one expires in ~20s if nobody answers)
[00:09] TASK_STATE_INPUT_REQUIRED  "Waiting for approval: Client dinner, $85"
...
[00:21] TASK_STATE_FAILED  "expense approval request expired unapproved"

final state: TASK_STATE_FAILED
```

Don't run the `--decision` command for this one. `orchestrations/approval.py`
follows docs/06 §5.3's two rules exactly: **always cancel the losing
timer** (only matters on the approval-wins branch, since there's nothing
to cancel on the deadline-wins side — a late `raise_event` against an
already-completed instance is a documented no-op, not an error this code
guards against), and **every wait has a deadline** — this sample's default
is 14 days (`DEFAULT_TIMEOUT_SECONDS`), not "forever."

## ⚠ What's NOT fully verified here

Same open seam as `../01-durable-hello-world-status` (see that sample's
README, "What's NOT fully verified here" — it applies here unchanged): the
`_current_client` contextvar bridge between a Functions HTTP trigger's
binding-injected `DurableOrchestrationClient` and a hand-built ASGI app is
this sample's own proposal, not a documented Microsoft pattern. This
sample adds one more call through that bridge, `client.raise_event(...)`
— its signature is cited from `docs/06-tier3-durable-agents.md` §5.3's own
text, same standard of evidence already used there for `client.start_new`/
`client.terminate`, and **not** independently re-verified against the
`azure-functions-durable` package (not installed in this dev environment —
these samples are meant to run inside a real Azure Functions host, not
this gateway repo's own dev container).

Also explicitly NOT done: a real Teams `conversationReference`
proactive-message flow (docs/06 §4.3, and the same §7 checklist's "Teams
conversationReference stored and proactive delivery tested" item).
`activities/request_approval.py` logs the exact `client/approve.py`
invocation instead — this sample's client script IS its approval channel.
Wiring a real Teams bot into `request_approval`/`notify_timeout` is
mechanically the same activity-calls-an-external-API pattern already used
for `notify`, just pointed at a different endpoint; not built here because
it would need a Teams app registration this sample has no way to provide.

**Reaper carve-out:** already true, not something this sample had to add.
`migrations/0001_init.sql`'s `gw_task_reaper` index only covers `state IN
('submitted','working')` — `input-required` was already excluded before
this sample existed. What this sample does add is the concrete case that
carve-out exists for: `lease_seconds: 1209600` (14 days) in
`apps.yaml.snippet.yaml`, since the *lease* (a separate mechanism from the
reaper's state filter — see `docs/08-open-items-and-experiments.md`) still
needs to be long enough to survive the wait, not just excluded from the
reaper's query.

**◆ Not decided, flagged not hidden:** `docs/02-decisions.md` D5 already
notes a real conflict — the default durable session TTL (14 days) versus
30-day conversation retention — and this sample's own default timeout
happens to land exactly on that boundary. An approval outliving its own
session TTL isn't exercised by anything here.

## Structure

```
03-hitl-durable/
├── README.md
├── src/
│   ├── function_app.py
│   ├── orchestrations/approval.py        # wait_for_external_event + task_any timeout race
│   ├── activities/
│   │   ├── notify.py                     # pushes gw.progress.v1 to the gateway (literal copy of sample 01's)
│   │   ├── request_approval.py           # stands in for "posts to Teams" -- see caveat above
│   │   ├── reimburse.py                  # the "do the thing" once approved
│   │   └── notify_timeout.py
│   ├── a2a/server.py                     # +INPUT_REQUIRED-aware resume routing vs. sample 01
│   ├── determinism.py
│   ├── host.json
│   ├── requirements.txt
│   └── local.settings.json.example
├── apps.yaml.snippet.yaml
└── client/
    └── approve.py    # request / respond, --timeout-seconds for the failure path
```

## Run it

```bash
# Durable Task Scheduler emulator + Azurite -- docs/06 §2.3
docker run -d --name dts-emulator -p 8080:8080 -p 8082:8082 mcr.microsoft.com/dts/dts-emulator:latest
docker run -d --name azurite -p 10000-10002:10000-10002 mcr.microsoft.com/azure-storage/azurite

cd src && pip install -r requirements.txt && func start
```

Wire it into the gateway the same way as the other T3 sample — merge
`apps.yaml.snippet.yaml` into `config/apps.yaml` (and its
`push_notification_allowlist` entry, since this card declares
`pushNotifications: true` too), then run `make gwlint` from the repo root
(zero waivers expected — `preview: allow` is required here for the same
reason `config/apps.example.yaml`'s `deep-research` entry states).

```bash
export GATEWAY_URL=http://localhost:8080
export GATEWAY_TOKEN=$(az account get-access-token --resource api://a2a-gateway --query accessToken -o tsv)
python client/approve.py "Client dinner, $85"
# ... wait for the INPUT_REQUIRED pause, then:
python client/approve.py <task_id> --decision approved
```
