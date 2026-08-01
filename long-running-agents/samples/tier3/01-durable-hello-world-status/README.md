# T3 sample 01 — durable hello world (FINE progress via push)

| | |
|---|---|
| Tier | **T3** (durable orchestration), fronted by this gateway |
| Stability | **Preview**, entirely: `agent-framework-durabletask`/`-azurefunctions`/`-a2a` are all pinned `--pre` (docs/01 §3) |
| RBAC | Function App managed identity needs `Durable Task Data Contributor` on the task hub and `Storage Blob Data Contributor` on the artifacts container (docs/06 §2.4) |
| Region features | Durable Task Scheduler + Flex Consumption availability in-region |

## What this shows

The exact same ~5 minutes of "work" as
`../../tier2/04-long-running-hello-world`, run through the same gateway,
watched with the same client script. Both samples narrate now — the
difference is *what kind*: T2's narration is automatic and coarse (one
line describing which tool is running, unable to see inside it), T3's is
explicit and fine-grained (the orchestrator's own code decides what each
step is called and when to report it). Run both back to back and compare
what shows up in `status.message` between `SUBMITTED` and `COMPLETED`.

## What you'll actually see

```
$ python client/watch_task.py "say hello" --app hello-world-t3
[00:00] TASK_STATE_SUBMITTED
[00:02] TASK_STATE_WORKING  "step 1/5: warming up the greeting engine"
[01:02] TASK_STATE_WORKING  "step 2/5: consulting the world about how it's doing"
[02:02] TASK_STATE_WORKING  "step 3/5: double-checking punctuation"
[03:02] TASK_STATE_WORKING  "step 4/5: polishing the exclamation mark"
[04:02] TASK_STATE_WORKING  "step 5/5: wrapping up"
[05:02] TASK_STATE_COMPLETED  "Hello, world!"
```

## Why this is true now, and wasn't when this sample was started

Building this sample surfaced a real bug, now fixed:
`GatewayAgentExecutor._follow_and_relay`
(`src/gateway/a2a_server/executor.py`) called
`updater.update_status(state)` with no `message` argument for *every*
tier, so `StatusEvent.detail` — the actual narration text this
orchestration's `notify` activity pushes — was computed, stored in
`gw_event`, and then silently dropped on the way to the A2A wire. Verified
directly against the installed `a2a-sdk` package (`TaskUpdater.update_status`
takes an optional `message: Message | None`) rather than assumed. The fix
is four lines: build a `Message` from `event.detail` via
`a2a.helpers.proto_helpers.new_text_message` when it's set, and pass it
through. That fix is what makes the trace above real gateway behavior
today, not aspirational — see the diff in
`src/gateway/a2a_server/executor.py` and the note in
`docs/08-open-items-and-experiments.md`.

Building the T2 counterpart (`../../tier2/04-long-running-hello-world`)
found a second, deeper problem on that side: `detail` was never populated
in the first place there, *and* the mechanism docs/05 §5.4 described for
populating it (`ctx.emit_custom_event`, a `gw.progress.v1` filter in
`follow()`) turned out not to exist in any installed package. That's since
been fixed too, with a different, real mechanism — narration automatically
derived from `Response.output` — see that sample's README and
`docs/08-open-items-and-experiments.md` item 16 for the full account.

## How the narration actually reaches the gateway

Per `docs/06-tier3-durable-agents.md` §4.1 and §5.4: **T3 does not stream**.
The orchestration below calls a `notify` activity between deterministic
`create_timer` waits; `notify` POSTs a `gw.progress.v1` payload straight to
the gateway's webhook (`src/gateway/api/webhooks.py`), matching
`ProgressPayload`'s real contract exactly (`task_id`, `kind="status"`,
`sequence`, `payload={"state", "final", "detail"}`). This bypasses this
sample's own A2A server entirely for progress — the A2A server
(`src/a2a/server.py`) only handles the initial `message/send` (which starts
the orchestration) and `tasks/cancel`; everything in between is a push, not
a poll, unlike T2's client-visible gateway-side polling.

```
Gateway ──message/send──▶ T3 A2A server ──client.start_new()──▶ orchestration
Gateway ◀──webhook POST── notify activity  (x5, one per step + completion)
Gateway ──tasks/get─────▶ T3 A2A server   (reconciliation only -- not exercised by this sample)
```

## Trace correlation, closing the loop

docs/05 §6.3 / docs/06 §6.3 both flag trace correlation as "the gap to
close first" — without a shared trace-id, one slow or failing turn can't
be followed from the chat client through the gateway into this
orchestration's own steps. The gateway's half is built
(`src/gateway/tracing.py`, `src/gateway/upstream/durable.py`): every
`SendMessage` `DurableAdapter.submit()`/`resume()` makes to this sample's
own A2A server carries a `traceparent` header. This sample demonstrates
the **receiving** half, since that part is necessarily this app's own
responsibility, not the gateway's:

1. `src/a2a/server.py`'s `execute()` reads `context.call_context.state
   ["headers"]["traceparent"]` — already populated for free by a2a-sdk's
   own `DefaultServerCallContextBuilder` (verified directly; this sample
   never installs a custom one) — and passes it into the orchestration's
   `client_input`.
2. `src/orchestrations/hello_world.py` extracts the trace-id segment once
   at the top of the run (a plain string operation — no clock, no
   randomness, no I/O, so it stays replay-safe per §5.1) and includes it
   in every `notify` activity's payload, under a `trace_id` key that isn't
   part of `ProgressPayload`'s own contract but is inert to the gateway's
   processing of it (`webhooks.py` stores the payload as opaque JSONB).

The payoff: an operator who has a trace-id from a gateway log line can
grep this sample's own Function App logs for the exact same value and
find every step of the matching orchestration run.

**Not retrofitted onto `../03-hitl-durable` or `../05-push-notifications`**
— both are literal-copy-derived from this sample's own structure, and the
same three-line change (read the header, thread it through client_input,
include it in `notify` payloads) applies identically to each, but doing so
wasn't part of this change and is left as a documented gap rather than a
silent one (see `docs/08-open-items-and-experiments.md`).

## ⚠ What's NOT fully verified here

Consistent with `docs/06`'s own posture on its T3 snippets (most of which
carry the same caveat): the seam between "an HTTP request arrives at the
FastAPI A2A app" and "a Durable Functions orchestration client, which the
platform only injects as a binding inside a Functions invocation" is
exactly the open hosting-model question in `docs/06` §2.5 — mixing a
hand-built FastAPI ASGI app with Functions' binding-injection model in one
process isn't demonstrated end to end anywhere in this project's docs.
`src/a2a/server.py`'s executor takes a `start_orchestration` callback
rather than constructing a `DurableOrchestrationClient` itself, and
`src/function_app.py` shows *where* that callback would be wired from a
real `durable_client_input` binding, marked where the seam actually is.
Everything else — the orchestrator's determinism (§5.1), the `notify`
activity's payload shape (verified against `webhooks.py` directly, not
guessed), the A2A server plumbing (verified against
`tests/test_durable_adapter_wire_format.py`'s real-a2a-sdk test double,
which this sample's `a2a/server.py` is structurally identical to) — is
grounded, not guessed.

## Structure

```
01-durable-hello-world-status/
├── README.md
├── src/
│   ├── function_app.py
│   ├── orchestrations/hello_world.py     # deterministic: timers, not sleep
│   ├── activities/notify.py              # pushes gw.progress.v1 to the gateway
│   ├── a2a/server.py                     # DefaultRequestHandler + InMemoryTaskStore
│   ├── determinism.py
│   ├── host.json
│   ├── requirements.txt
│   └── local.settings.json.example
├── apps.yaml.snippet.yaml
└── client/
    └── watch_task.py    # identical to the T2 sample's client, --app differs
```

## Run it

```bash
# Durable Task Scheduler emulator + Azurite -- docs/06 §2.3
docker run -d --name dts-emulator -p 8080:8080 -p 8082:8082 mcr.microsoft.com/dts/dts-emulator:latest
docker run -d --name azurite -p 10000-10002:10000-10002 mcr.microsoft.com/azure-storage/azurite

cd src && pip install -r requirements.txt && func start
```

Wire it into the gateway the same way as the T2 sample — merge
`apps.yaml.snippet.yaml` into `config/apps.yaml`, then run `make gwlint`
from the repo root (should pass with zero waivers: `preview: allow` is
required here too, for the reason `config/apps.example.yaml`'s
`deep-research` entry states).

```bash
export GATEWAY_URL=http://localhost:8080
export GATEWAY_TOKEN=$(az account get-access-token --resource api://a2a-gateway --query accessToken -o tsv)
python client/watch_task.py "say hello" --app hello-world-t3
```

## The deliberate failure path

```bash
python client/watch_task.py "say hello" --app hello-world-t3 --cancel-after 70
```

Same client, same `--cancel-after` behavior as the T2 sample — but here,
cancellation additionally has to reach into the running orchestration
(`client.terminate()`), not just an upstream Responses call. Watch for the
same "never optimistic" gap D7 describes: one or two more narrated
`WORKING` steps may still land after the cancel request, because the
in-flight `notify` activity that was already scheduled before termination
took effect still completes and still pushes its event — the gateway
reports what actually happened, not what was requested.
