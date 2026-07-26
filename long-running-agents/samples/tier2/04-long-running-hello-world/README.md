# T2 sample 04 — long-running hello world (COARSE progress only)

| | |
|---|---|
| Tier | **T2** (hosted agent), fronted by this gateway |
| Stability | Hosted agents are themselves a preview feature end to end — `FoundryHostedAdapter` always sends `Foundry-Features: HostedAgents=V1Preview` (docs/05 §3, D10) |
| RBAC | Gateway identity needs `UserIdentityImpersonation` on the agent (docs/00 §"Tier 2 identity — the trap") |
| Region features | The agent's model deployment must support hosted-agent regions (docs/05 §1) |

## What this shows

The dumbest possible T2 agent: it does one thing (sleep ~5 minutes) and
then says "Hello, world!" No tools, no multi-agent orchestration, nothing —
this sample exists purely to show **what a client sees while a T2 task is
in flight when the agent never narrates its own progress**.

Run this side by side with `../../tier3/01-durable-hello-world-status`,
which is the *identical* 5-minute wait, fronted by the same gateway, using
the same client script. The only difference is what the agent chooses to
report — and that difference is entirely visible on the wire.

## What you'll actually see

```
$ python client/watch_task.py "say hello"
[00:00] TASK_STATE_SUBMITTED
[00:02] TASK_STATE_WORKING
[00:32] TASK_STATE_WORKING
[01:02] TASK_STATE_WORKING
[01:32] TASK_STATE_WORKING
  ...                              <- identical WORKING, every 30s, for ~5 minutes
[05:01] TASK_STATE_COMPLETED  "Hello, world!"
```

Every poll returns the same bare state. There is no `status.message` on
any of the intermediate `WORKING` updates — nothing to distinguish minute 1
from minute 4. This is real gateway behavior, not a simplification for the
sample, for **two compounding reasons**, both worth understanding:

1. **This particular agent** (`agent/main.py`) never emits a
   `gw.progress.v1` custom event — it just sleeps. Nothing to report even
   in principle.
2. **Even an agent that did emit one wouldn't help yet.** `docs/05 §5.4`
   documents "T2 `FINE`" as a decision: an agent emits `gw.progress.v1`
   events on its response stream, and "this is a filter in `follow()` — no
   new transport" on the gateway side. That filter does not exist in this
   codebase today — `FoundryResponsesAdapter.follow()`
   (`src/gateway/upstream/foundry_responses.py`) constructs every
   `StatusEvent` with `detail` left at its default `None`, always, and
   `FoundryHostedAdapter.capabilities` (`foundry_hosted.py`) still declares
   `progress=ProgressFidelity.COARSE` with a comment claiming "promoted to
   FINE by the gw.progress.v1" mechanism — a mechanism that was decided,
   documented, and never built. This sample is what surfaces that: a T2
   agent's progress fidelity is COARSE-only *by gateway design*, not just
   because this particular hello-world agent is lazy. Building that filter
   is real future work, tracked as a fresh item, not attempted here — see
   `docs/08-open-items-and-experiments.md`.

Contrast with T3, where the equivalent gap **was** just closed (this
sample's build surfaced and fixed a real bug: `GatewayAgentExecutor`
previously dropped `StatusEvent.detail` on the floor for every tier — see
`../../tier3/01-durable-hello-world-status/README.md` "What you'll
actually see"). T2's gap is one layer further upstream — the adapter never
produces a `detail` to drop in the first place — which is why fixing it is
out of scope here and T2's story stays "no useful state messages" even
after that fix.

## Structure

```
04-long-running-hello-world/
├── README.md
├── agent/
│   ├── main.py            # protocol host -- sleeps, never narrates
│   └── requirements.txt
├── apps.yaml.snippet.yaml  # add to config/apps.yaml
└── client/
    └── watch_task.py        # SendMessage + poll GetTask against the gateway
```

## Deploy the agent

Follow `docs/05-tier2-hosted-agents.md` §2.2 (`azd ai agent init` /
`azd provision` / `azd deploy`) pointed at `agent/main.py` — this sample
doesn't ship its own `azure.yaml`/Dockerfile since that's identical
boilerplate to any T2 agent; the only thing specific to this sample is
`main.py`'s handler.

## Wire it into the gateway

Add to `config/apps.yaml` (see `apps.yaml.snippet.yaml` for the exact
block):

```yaml
apps:
  - name: hello-world-t2
    tier: t2
    upstream: hello-world-t2-hosted
    default_mode: long
    preview: allow      # T2 is preview end-to-end -- D10

upstreams:
  - id: hello-world-t2-hosted
    tier: t2
    project_endpoint: ${FOUNDRY_PROJECT_ENDPOINT}
    agent_name: hello-world-t2
    identity: per_user
```

Run `make gwlint` from the repo root after merging this in — it should
pass with zero waivers (`preview: allow` is required here for exactly the
reason `config/apps.example.yaml`'s own `ticket-triage` entry states).

## Run it

```bash
export GATEWAY_URL=http://localhost:8080
export GATEWAY_TOKEN=$(az account get-access-token --resource api://a2a-gateway --query accessToken -o tsv)
python client/watch_task.py "say hello"
```

## The deliberate failure path

```bash
python client/watch_task.py "say hello" --cancel-after 10
```

Sends the message, waits 10 seconds, then calls `CancelTask`. Watch the
printed state: it does **not** jump straight to `TASK_STATE_CANCELED`. Per
D7 ("never optimistic" cancellation, `docs/02-decisions.md`), the gateway
only reports canceled once the upstream confirms it — so you'll see one or
two more `WORKING` polls after the cancel request before the state
actually flips. That gap is the point: a client that assumed cancel was
instantaneous would render a stale "canceling..." UI for longer than it
expects, and this sample makes that visible instead of hiding it.
