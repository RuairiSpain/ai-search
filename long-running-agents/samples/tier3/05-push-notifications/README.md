# T3 sample 05 — push notifications instead of polling

| | |
|---|---|
| Tier | **T3** (durable orchestration), fronted by this gateway |
| Stability | **Preview**, entirely: `agent-framework-durabletask`/`-azurefunctions`/`-a2a` are all pinned `--pre` (docs/01 §3) |
| RBAC | Function App managed identity needs `Durable Task Data Contributor` on the task hub (docs/06 §2.4) -- this sample has no artifacts, so no `Storage Blob Data Contributor` grant needed |
| Region features | Durable Task Scheduler + Flex Consumption availability in-region |

Not part of `docs/02-decisions.md`'s original target sample catalogue (see
that section's own note on this addition) — every other T2/T3 sample's
card already declares `pushNotifications: true`, but nothing in this repo
actually registered one and watched it arrive until this sample.

## What this shows

`CreateTaskPushNotificationConfig`, end to end, against a real receiver —
not just the gateway-side unit test
(`tests/test_a2a_api.py::test_push_notification_config_delivers_on_completion`,
which this sample's wire format is copied from). The orchestration itself
is the least interesting part: a shortened copy of
`../01-durable-hello-world-status`'s hello-world, three steps instead of
five, 10 seconds apart instead of 60 — the point here is that
`client/push_demo.py` never calls `GetTask` even once. It registers a
callback, then blocks on its own tiny local HTTP receiver, printing each
status change **the instant the gateway's `BasePushNotificationSender`
delivers it** — no polling loop, no `POLL_INTERVAL_S`, nothing to tune for
latency-vs-load.

## What you'll actually see

```
$ python client/push_demo.py
receiver listening on http://localhost:8899/push
[00:00] task task_a1b2c3d4 submitted
[00:00] (expected) blocked non-allowlisted callback: push-notification URL host 'not-allowlisted.evil.example' is not on the configured allowlist
[00:00] registered push callback -> http://localhost:8899/push
[00:00] waiting on pushes (no GetTask polling from here on)...
[00:03] PUSH  TASK_STATE_WORKING  [token verified]
[00:13] PUSH  TASK_STATE_WORKING  [token verified]
[00:23] PUSH  TASK_STATE_WORKING  [token verified]
[00:33] PUSH  TASK_STATE_COMPLETED  "Hello, world!"  [token verified]
```

## The deliberate failure path

It's baked into the happy path above, not a separate invocation: every run
first tries to register a callback at `https://not-allowlisted.evil.example/cb`
and asserts the gateway rejects it. This is `gwlint` rule `L023` doing its
job at request time, not just at config-review time —
`GatewayPushConfigStore.set_info()` (`src/gateway/a2a_server/push_config.py`)
checks `urlparse(url).hostname` against `push_notification_allowlist` on
every single registration attempt, closed by default: an app that forgets
the allowlist entry can't accidentally let a push go anywhere.

If you want to see the *other* kind of failure — a receiver that would
have accepted a forged push — try commenting out the `assert received_token
== NOTIFICATION_TOKEN` line in `client/push_demo.py` and posting a fake
payload to `http://localhost:8899/push` yourself with `curl` while the
script waits. It'll print the forged line right alongside the real ones.
That assertion existing at all is the actual security boundary here, not
a decoration — a receiver that logs a token mismatch instead of rejecting
on it is accepting pushes from anyone who can reach its URL.

## ⚠ What this sample is honest about NOT being

`client/push_demo.py`'s receiver is `http.server.BaseHTTPRequestHandler`
on plain HTTP, no TLS, no auth beyond the one shared token, running on
`localhost` because in local dev the gateway and this script run on the
same machine. **This is not a production callback receiver** — a real one
needs TLS termination, a stable public (or VNet-reachable) address, and
should treat a token mismatch as an incident, not a printed line. The only
thing this sample demonstrates is the *registration and delivery*
mechanics; hosting a receiver for real is a separate, ordinary web-service
concern this repo doesn't take a position on.

`push_notification_allowlist` entry needed is `localhost`, which only
makes sense for this exact local-dev topology — see the note in
`apps.yaml.snippet.yaml`.

## Structure

```
05-push-notifications/
├── README.md
├── src/                                   # literal copy of ../01-durable-hello-world-status's,
│   │                                       # shortened -- see orchestrations/hello_world.py
│   ├── function_app.py
│   ├── orchestrations/hello_world.py
│   ├── activities/notify.py
│   ├── a2a/server.py
│   ├── determinism.py
│   ├── host.json
│   ├── requirements.txt
│   └── local.settings.json.example
├── apps.yaml.snippet.yaml
└── client/
    └── push_demo.py    # the actual point of this sample
```

## Run it

```bash
# Durable Task Scheduler emulator + Azurite -- docs/06 §2.3
docker run -d --name dts-emulator -p 8080:8080 -p 8082:8082 mcr.microsoft.com/dts/dts-emulator:latest
docker run -d --name azurite -p 10000-10002:10000-10002 mcr.microsoft.com/azure-storage/azurite

cd src && pip install -r requirements.txt && func start
```

Merge `apps.yaml.snippet.yaml` into `config/apps.yaml`, including its
`push_notification_allowlist: [localhost]` entry, then `make gwlint` from
the repo root (zero waivers expected).

```bash
export GATEWAY_URL=http://localhost:8080
export GATEWAY_TOKEN=$(az account get-access-token --resource api://a2a-gateway --query accessToken -o tsv)
pip install -r client/requirements.txt
python client/push_demo.py
```
