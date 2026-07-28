# Artifacts and Code Interpreter

Tier-agnostic policy: one gateway-owned blob container for all tiers.
Every tier's artifact channel funnels through the same store, the same
`gw_artifact` index (`03-postgres-schema.md`), and the same `artifact_url()`
adapter method (`01-gateway-config-and-adapter-contract.md`).

**Scope note:** T1 is not a gateway tier (`00-tier-model-and-concepts.md`),
so sections below marked "(T1)" describe a mechanism, not a tier this
codebase fronts — the code-interpreter container poll-and-harvest loop they
describe is real and implemented, it just lives on `FoundryHostedAdapter`
(T2) in this codebase now, inherited from the shared `FoundryResponsesAdapter`
base (`src/gateway/upstream/foundry_responses.py`). The T1-vs-T2 comparisons
here are kept because the underlying reasoning (why blob storage, not a
session) is unchanged by that — read "T1" in this doc as "whichever tier is
actually harvesting citations through this base class."

## 1. Why not a T2 session as the artifact store

Hosted-agent sessions look tempting because identity scoping is already
enforced, but they're the wrong substrate:

- **Sessions are scoped to one agent.** You can't share a session across
  agents, so a T1 artifact would need a hosted agent standing there purely
  as a filing cabinet.
- **They're still not durable** — 30 days of inactivity and the state is
  permanently deleted, and a pure-storage session is by definition
  inactive. You'd have traded a 1-hour clock for a 30-day one.
- **Reads depend on compute.** After 15 minutes idle the sandbox is
  deprovisioned; a download may pay a cold start.
- **It inverts the layering.** T1 durability would depend on a T2 agent's
  deployment lifecycle, and both hosted agents and the Session Files API
  are preview. Blob storage isn't.

So: one gateway-owned blob container, and the prefix scheme below.

## 2. What actually removes the brittleness

**1. Harvest during the poll loop, not at completion.** This is the real
fix — a code interpreter container lives about an hour; a background
response can run longer. If an intermediate chart is produced at minute 10
and you only harvest at minute 75, the container is already gone. Fetch on
every poll where a new `container_file_citation` appears.

**2. Prefix scheme, tier-agnostic:**

```
artifacts/{app}/{principal_hash}/{context_id}/{task_id}/{artifact_id}-{name}
```

Each level earns its place — `app` for per-app lifecycle rules,
`principal_hash` so a deletion request is one prefix delete, `context_id`
for conversation cleanup, `task_id` so a re-run doesn't collide. Azure blob
lifecycle policies match on prefix, which is the whole reason to get this
right up front rather than reorganising later.

**3. Copy T2 and T3 artifacts in too.** Tempting to leave hosted-agent
files in their session store since they already last 30 days, but then
your clients have three download paths. One store, one contract, one
expiry policy.

**4. Never hand out raw blob URLs.** Mint a short-lived **user delegation
SAS** per download — Entra-backed, no account key — after checking
`gw_artifact.task_id → gw_context.principal_subject` matches the caller.

**5. Make harvest idempotent.** Key on `(task_id, artifact_id)`,
conditional-create the blob, and a duplicate webhook becomes a no-op.

**6. Never let a client observe "completed" before its artifacts are
harvested.** A real race, not a theoretical one — `FoundryResponsesAdapter
.follow()` used to yield a completed poll's terminal `StatusEvent` (i.e.
`final=True`) *before* that same poll's `ArtifactEvent`s, so a client that
called `GetTask` the instant it saw `TASK_STATE_COMPLETED` could get a task
with no artifacts yet: the harvest — a real network copy into blob storage
— was still in flight. `samples/tier2/02-per-user-isolated-storage`'s
`fake_chat_ui.py` hit this directly and used to paper over it with a
"harvest may still be in flight, re-run GetTask" message. Fixed by yielding
artifacts first: `_follow_and_relay` (`src/gateway/a2a_server/executor.py`)
awaits each yielded event in turn (harvest + `add_artifact()` for an
artifact, `update_status()` for status), and a2a-sdk's own `EventConsumer`
persists its single event queue strictly FIFO — so whichever event this
adapter yields first is guaranteed persisted first. **This is a gateway
responsibility for T2** (the gateway controls both artifact detection and
terminal-status emission from the same poll) but an **orchestrator-author
responsibility for T3**, since `DurableAdapter.follow()` only relays events
in whatever order the T3 app's own webhook pushes land in — get this
backwards in your own orchestrator and the same race reappears on T3. The
reference orchestrator in `06-tier3-durable-agents.md` §5.2 already gets
this right (`harvest_artifact` activity awaited, *then* the `"completed"`
notify) — follow that order, not the reverse.

That turns the container reference into a transient detail rather than the
canonical location — which is what "brittle" really meant.

The `gw_artifact_unharvested` index (`03-postgres-schema.md`) is the one to
wire an alert to. A row sitting in `pending` means a harvest lost its race
with the container TTL, and unlike most failures in this system it isn't
retryable — the bytes are gone. If that fires more than rarely, the poll
interval is too slow for the workload.

**Open, not yet decided:** retention period per app beyond the D5 default,
and whether artifacts of record need blob immutability.

## 3. Code interpreter container lifecycle (T1)

### Reframe: bound it, don't kill it

The delete API does exist on the Responses surface —
`DELETE /v1/containers/{id}` returns
`{"object": "container.deleted", "deleted": true}` — and containers created
in auto mode are also accessible through the containers endpoint, so it's
reachable even when you didn't create it. Verify it's exposed on Foundry's
`/openai/v1/containers/` path for your API version; the file-content route
definitely is.

But the better lever is **explicit container mode**. You create the
container yourself via the containers endpoint, specifying memory limit,
and assign its id as the `container` value in the tool configuration. That
includes a TTL you choose:

```json
{ "name": "gw-task-abc", "memory_limit": "1g",
  "expires_after": { "anchor": "last_active_at", "minutes": 10 } }
```

Ten minutes instead of an hour, enforced by the service, with no terminal
condition for you to detect. That's strictly safer than issuing a delete,
because a missed delete degrades to a short timeout rather than a long
one.

### The gotcha that ruins the naive version

Any container operation — retrieving it, or adding or deleting files —
automatically refreshes `last_active_at`.

Your harvest reads files. **So harvesting extends the container's life**,
which is the opposite of what you're trying to do. Polling every few
seconds and reading annotations keeps it warm indefinitely. If you were
counting on idle expiry to reclaim it, that won't happen while you're
still fetching. In testing, seeing a container with a nominal 10-minute
`expires_after` still alive at 40 minutes is the poll loop doing its job,
not a bug.

### Three reasons not to lead with delete

1. **The saving may be zero.** Microsoft support guidance states there's
   no separate charge for creating the container in Foundry, since its
   lifecycle is tied to the session and the compute is folded into overall
   pricing. That conflicts with the general "code interpreter has
   additional charges" line, which likely means per-session or
   per-invocation rather than per-idle-minute. **Check an actual bill
   before engineering for this** — you may be optimising a line item that
   doesn't exist.

2. **You destroy usable state.** You can't move a container from expired
   back to active; you create a new one and re-upload, and any state in
   the old container's memory such as Python objects is lost. A user
   saying "now make that log scale" then pays a re-upload and full
   recompute — almost certainly more than the sandbox cost.

3. **You can't detect "the interpreter finished."** There's no terminal
   tool signal, only a terminal *response*. And the conversation may
   continue.

### Safe sequence if you do reclaim

```python
async def _finalise(self, task, ref):
    if task.state not in TERMINAL:
        return                                  # never mid-response
    await self._harvest(task, ref)              # refreshes TTL — expected
    if not await self._all_stored(task.task_id):
        return                                  # verified by sha256, not HTTP 200
    if self._container_policy != "reclaim":
        return                                  # per-app; refinement apps opt out
    try:
        await self._containers.delete(ref.container_id)
    except Exception:
        log.info("container %s reclaim failed; idle TTL will collect it",
                 ref.container_id)              # NEVER fails a completed task
```

Order matters: terminal → harvest → verify → mark stored → *then* delete,
best-effort. Cleanup must never be able to fail a request the user
already got an answer to.

`container_policy: reuse | reclaim` is a legitimate per-app setting — an
iterative analysis app and a one-shot report app genuinely want different
behaviour.

### Related mitigation: don't retype data through the model

There's a known failure mode where containers expire mid-run on long
background responses even with activity. The defence is to **upload
source data to the Files API once and reference by `file_id`** rather than
living inside a single container: files persist independently, so a fresh
container can re-mount them. Costs you the in-memory state, saves you the
inputs.

## 4. MCP → code interpreter handoff

MCP results land in the **model's context as text**, and the model then
has to retype that data into the Python it writes. For a handful of rows
that's fine. For a real dataset it's expensive and lossy — the model will
silently truncate or paraphrase numbers.

The better pattern: get the data to a *file*, then pass it via
`container.file_ids` so the sandbox reads it directly and the model never
handles the values. If your MCP server can return a URI or blob reference
rather than inlined rows, take that path. If it can only return text, cap
the row count in the agent instructions and expect approximations above
it. This is the same one-upload-referenced-by-id pattern as the container
handoff above — never retyped by the model into generated code.

## 5. How the image comes back (T1)

Code Interpreter attaches directly to a `kind: prompt` definition:

```json
{
  "name": "chart-agent",
  "definition": {
    "kind": "prompt",
    "model": "<MODEL_DEPLOYMENT>",
    "instructions": "You are a data visualization assistant. When asked to create charts, write and run Python code using matplotlib to generate them.",
    "tools": [
      { "type": "code_interpreter",
        "container": { "type": "auto", "file_ids": ["<FILE_ID>"] } }
    ]
  }
}
```

The response includes `container_file_citation` annotations with the
generated file details. Fetch bytes yourself:

```
GET $FOUNDRY_PROJECT_ENDPOINT/openai/v1/containers/<CONTAINER_ID>/files/<FILE_ID>/content
```

Two operational facts that matter more than the API shape:

- The sandbox uses dynamic sessions in Azure Container Apps with Hyper-V
  isolation per session, and a code interpreter session stays active for
  up to one hour with an idle timeout. **The file is ephemeral.** Copy it
  out at task completion or it's gone.
- If no file comes back, rephrase the prompt to explicitly request file
  output. The model often answers in prose instead of saving a PNG. Put
  "save the chart to a file" in the instructions, not just in the user's
  message.

Also: code interpreter bills separately from tokens.

### Turning that into one A2A message

Text and image travel together as multiple `Parts` in a single `Message` —
that's exactly what A2A parts are for:

```python
async def _harvest(self, resp, task_id) -> list[Part]:
    parts: list[Part] = [TextPart(text=resp.output_text)]
    for ann in _container_file_citations(resp):
        raw = await self._files.container_content(ann.container_id, ann.file_id)
        # Copy out NOW. The container dies within the hour.
        uri = await self._blob.put_and_sign(
            f"{task_id}/{ann.filename}", raw, ttl=timedelta(hours=24))
        parts.append(FilePart(name=ann.filename, mime_type="image/png", uri=uri))
    return parts
```

Never inline base64 — a matplotlib PNG through JSON-RPC will wreck your
event rows and your Postgres row size.

Worth noting this vindicates a design choice made elsewhere: Foundry's own
incoming A2A is **text-only**, so an image could never traverse it.
Because the gateway is the A2A server rather than a proxy in front of
Foundry's, it can emit `FilePart` freely.

## 6. Correction applied during merge: T1 does have an artifact channel

An early draft set `Capabilities.artifacts = False` for tier 1 on the
reasoning that prompt agents have no filesystem. That conflated two
things. There's no *persistent* filesystem, but code interpreter produces
retrievable files with a different lifecycle — container-scoped and
roughly an hour, versus the hosted agent's 30-day session store. Tier 1
does have an artifact channel; it's just a fragile one that the gateway
must actively harvest (§2 above). `Capabilities.artifacts = True` for T1 in
`01-gateway-config-and-adapter-contract.md` reflects the corrected
position.

This also means the escalation-table row in `00-tier-model-and-concepts.md`
§4 reads "needs artifacts that outlive the response" rather than "emits
downloadable files" — a chart returned inside the same turn is fine on
tier 1; a document the user comes back for tomorrow is not, unless the
gateway owns the copy-out. Since the gateway does own it in this design,
that constraint is weaker than a first reading suggests.
