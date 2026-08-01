# T2 sample 04 — long-running hello world (COARSE, automatic progress)

| | |
|---|---|
| Tier | **T2** (hosted agent), fronted by this gateway |
| Stability | Hosted agents are themselves a preview feature end to end — `FoundryHostedAdapter` always sends `Foundry-Features: HostedAgents=V1Preview` (docs/05 §3, D10) |
| RBAC | Gateway identity needs `UserIdentityImpersonation` on the agent (docs/00 §"Tier 2 identity — the trap") |
| Region features | The agent's model deployment must support hosted-agent regions (docs/05 §1) |

## What this shows

A near-minimal T2 agent: it calls exactly one tool (`slow_then_greet`,
which takes ~5 minutes) and then says "Hello, world!" No multi-agent
orchestration, no application code calling any progress API — this sample
shows **what a client sees while a T2 task is in flight, narrated entirely
by the platform's own tool-call bookkeeping, with zero agent-side effort**.

Run this side by side with `../../tier3/01-durable-hello-world-status`,
which is the *identical* 5-minute wait, fronted by the same gateway, using
the same client script. Both narrate — the difference is *how much*, and
*who controls it*.

## What you'll actually see

```
$ python client/watch_task.py "say hello"
[00:00] TASK_STATE_SUBMITTED
[00:02] TASK_STATE_WORKING  "running tool: slow_then_greet"
[00:32] TASK_STATE_WORKING  "running tool: slow_then_greet"
[01:02] TASK_STATE_WORKING  "running tool: slow_then_greet"
  ...                              <- SAME line, every 30s, for ~5 minutes
[05:01] TASK_STATE_COMPLETED  "Hello, world!"
```

One narration line, repeated verbatim for the whole run: the gateway knows
*which* tool call the agent is inside, but not what that tool call is
actually doing internally — `slow_then_greet` is opaque to the platform
once it starts, so "running tool: slow_then_greet" at minute 1 and at
minute 4 are indistinguishable. Contrast with T3
(`../../tier3/01-durable-hello-world-status`), where the *orchestrator's
own code* chooses to report five distinct sub-steps explicitly.

## Why this is what T2 actually does

`FoundryResponsesAdapter.follow()` (`src/gateway/upstream/
foundry_responses.py`) derives narration from `Response.output` — a real,
standard field the platform attaches to every polled response, listing
what the model has called so far (`function_call`, `mcp_call`,
`code_interpreter_call`, `reasoning`, `message`, ...). `_narrate()` reads
the most recent item and produces a short line from it — automatically,
for **every** T2 agent, no agent-side code required. This is genuinely
real and genuinely automatic, which is *better* than what an earlier
version of this sample (and of `docs/05-tier2-hosted-agents.md` §5.4)
described: an agent-emitted `gw.progress.v1` custom event that authors
would have had to remember to call. That API turned out not to exist
anywhere — confirmed by downloading and inspecting the real, installed
`agent-framework-foundry` and `azure-ai-agentserver-responses` packages —
see `docs/08-open-items-and-experiments.md` item 16 for the full account,
and `docs/05` §5.1/§5.4 for the corrected reference material.

What this mechanism can't do is narrate *inside* a single tool call — it
only sees tool-call boundaries, not whatever that tool is doing while it
runs. That's exactly why `Capabilities.progress` stays declared `COARSE`,
not `FINE`, even now: it's real, automatic narration, but coarse-grained by
what it can observe, not by any remaining gap in the gateway. An agent
that calls several short tools in sequence would narrate each one as it
starts; this sample's single long-running tool call is the case where that
still leaves one static line for five minutes.

## Structure

```
04-long-running-hello-world/
├── README.md
├── agent/
│   ├── main.py            # protocol host -- one slow tool call, no progress code
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
