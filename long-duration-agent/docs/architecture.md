# Architecture

## Flow

```text
Chat UI (Teams / Copilot Studio / custom)
  │  POST /invocations
  │  Authorization: user Entra token (or OBO context from the channel)
  │  { prompt, operation_id }
  ▼
Hosted Agent - Invocations endpoint (hosted_agent/app.py)
  │
  ▼
Durable MAF Workflow (durable/pipeline.py), one Executor per step, checkpointed after each:
  │
  ├─ Validate          → reject if prompt > 1,000,000 characters; content safety guardrail
  ├─ SSE status: "The agent is working..."
  ├─ Translate          → es-ES, via Foundry/Azure OpenAI chat client
  ├─ SSE status: "The text has been translated."
  ├─ Save Markdown      → hosted-agent local scratch workspace (temporary only)
  ├─ wait 5s
  ├─ SSE status: "The artifact was created successfully."
  ├─ Steering gate       → any queued steering messages? (see "Steering while the agent is
  │                         working" below) - if yes, HITL confirm, then loop back to Validate
  ├─ wait 2s
  ├─ Upload             → Blob Storage, users/<tenant>/<object-id>/<artifact-id>.md
  ├─ SSE status: "The artifact was saved to secure storage."
  ├─ Delete local copy  → cleans up the hosted-agent's scratch file
  ├─ Mint SAS link      → generate_download_url() - a real, signed Blob SAS URL, freshly issued
  └─ SSE artifact: { artifact_id, download_url, expires_at }
  │
  ▼
Storage Account (infra/storage-public.bicep)
  - public network access enabled; anonymous blob access disabled
  - 1-day blob lifecycle policy
  - every read/write/delete logged to Log Analytics (diagnostic settings)
  - only the hosted agent's managed identity has standing RBAC access (upload/delete,
    and permission to mint the User Delegation Key that signs each SAS)
  │
  ▼
User's browser ── GET {download_url} (a signed SAS URL) ──▶ Blob Storage directly
                                                             (no broker/proxy in between)
```

## Local development storage backends

`storage/blob_store.py` has three backends behind one `BlobStore` interface
(`LDA_STORAGE_BACKEND`):

- `local` (default) - `LocalDiskBlobStore`, pure Python, no external process. Fastest option
  and what the test suite uses.
- `azurite` - `AzureBlobStore` pointed at a local
  [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) instance via
  connection string. This is the *same* `AzureBlobStore` class and the *same*
  `azure-storage-blob` SDK calls used against real Azure - Azurite implements the real Blob
  REST API, so this exercises the actual production code path (auth handshake shape,
  container/blob semantics, SDK error types) without any cloud resources. Verified manually:
  running Azurite locally (`npx azurite-blob --skipApiVersionCheck` - the flag works around
  newer SDK versions sending an API version an older Azurite build doesn't recognize yet) and
  pointing the pipeline at it produces and reads back the same bilingual Markdown artifact as
  the `local` backend, through the real SDK.
- `azure` - `AzureBlobStore` against a real, public-network storage account via `account_url` +
  `DefaultAzureCredential` (Managed Identity in production, `az login` for a developer) - see
  "Public storage + SAS" below.

Azurite's connection string uses its own published, well-known development account key -
not a secret, and not usable against any real Azure account.

`AzureBlobStore` uses `azure.storage.blob.aio.BlobServiceClient` / `azure.identity.aio.
DefaultAzureCredential` deliberately - not the sync clients. Its methods are `await`ed from
request-handling code (executors), so a sync client would block the whole asyncio event loop
for every upload/download/delete's full network round-trip, stalling every other concurrent
request on the process. It also normalizes `azure.core.exceptions.ResourceNotFoundError` (what
the SDK actually raises for a missing blob) into `FileNotFoundError`, so callers (`cleanup.py`,
`StopExecutor`) can stay backend-agnostic instead of special-casing the Azure SDK's own
exception hierarchy.

`AzureBlobStore` holds a `BlobServiceClient` (not a bare `ContainerClient`) specifically so it
can also call `get_user_delegation_key()` - a `BlobServiceClient`-only method needed for
`generate_download_url`'s SAS signing (see "Public storage + SAS" below); the container client
used for upload/download/delete is derived from it via `get_container_client()`, so there's still
only one credential/session per store instance.

## Why a durable MAF Workflow instead of a hand-rolled step runner

`agent_framework.WorkflowBuilder` lets each step be an `Executor` connected by edges; when
built with `checkpoint_storage=...`, the framework checkpoints state after every step
automatically. Running with `workflow.run(message, stream=True, checkpoint_storage=...)`
yields a live stream of `WorkflowEvent`s - both the framework's own lifecycle events and
custom ones a handler adds via `ctx.add_event(...)`, which is how the four user-facing status
messages and the final artifact link are surfaced.

Resumability is a first-class, tested feature of the same API: `workflow.run(checkpoint_id=...,
checkpoint_storage=...)` restores the last completed step and continues, rather than
restarting the pipeline. `durable/engine.py` uses this for idempotent replay: submitting the
same `operation_id` twice - because the chat UI retried, or reconnected after a drop -
resumes forward instead of re-translating or re-uploading.

This is a demo, but it is not a toy abstraction sitting on top of raw `asyncio.sleep` calls:
it is the same `Workflow` object Microsoft's own Azure Functions Durable Task extension
(`agent-framework-durabletask`) hosts for production-scale, cross-process durability. See
"Scaling beyond a single host" below for the concrete migration path - no rewrite of
`pipeline.py` is required.

## Steering while the agent is working

The user can send additional text after the initial prompt, before the artifact has reached
Blob Storage:

```text
POST /invocations/{operation_id}/steer   { "text": "..." }
```

This only queues the message (`storage/metadata_store.py`'s `steering_messages` table) - it
never interrupts the running pipeline directly. The workflow has exactly one place that looks
at that queue: `SteeringGateExecutor`, which runs right after the "artifact created" wait and
right before Upload (`durable/pipeline.py`). That single checkpoint is what makes "only before
the file has been copied to Blob Storage" true: once Upload has run, the gate is never visited
again for that operation, so a `/steer` call on a completed operation is rejected outright
(`409`, `OperationNotSteerableError`) rather than silently doing nothing.

If the gate finds nothing queued, it proceeds straight to Upload - the common case is
completely unaffected by this feature, with no extra latency or events.

If one or more messages are queued, the gate:

1. Concatenates them with the current prompt (`state.prompt + queued messages, in arrival order`).
2. Emits a `status` event (`steering_detected`).
3. Issues a **HITL (human-in-the-loop) request** via `ctx.request_info(...)` - the workflow
   suspends here. The SSE stream delivers this as `event: hitl_request`, carrying the full
   concatenated text so the UI can show the user exactly what would be translated:

   ```json
   {"stage": "hitl_pending", "request_id": "...", "question": "...", "full_text": "..."}
   ```

4. The chat UI answers with:

   ```text
   POST /invocations/{operation_id}/respond   { "decision": "yes" | "edit" | "stop", "edited_text"?: "..." }
   ```

   - **`yes`** - translate the concatenated text shown in the HITL request.
   - **`edit`** - translate `edited_text` instead; it fully replaces the prompt (not another
     concatenation).
   - **`stop`** - cancel the operation. `StopExecutor` deletes the hosted agent's local scratch
     file (and, defensively, any blob that might exist - normally none does, since this gate
     always runs before Upload) and the operation ends with no download link.

   Both `yes` and `edit` route back to **Validate**, not directly to Translate - so an edited
   or concatenated prompt is re-checked against the 1,000,000-character limit like any other
   prompt, and the pipeline genuinely "starts again" from there (a fresh "The agent is working..."
   status, then Translate, Save Markdown, the 5s wait, and the gate again). This is a real loop
   in the workflow graph (`steering_gate → validate → ... → steering_gate`), not a special case:
   it keeps looping for as many rounds of steering as the user sends, and only reaches Upload
   once a pass through the gate finds the queue empty.

Resuming a HITL pause uses the same checkpoint/`responses={}` mechanism as any other resume:
`workflow.run(checkpoint_id=..., responses={request_id: SteeringDecision(...)}, ...)`. The
operation's `pending_request_id` (persisted on the `operations` row when the pause happens) is
what lets a *separate* HTTP call - which has no access to the original SSE stream - resume the
right pending request. Ownership is checked the same way as everywhere else: `/steer` and
`/respond` both 403 if the caller isn't the operation's original owner, and `/respond` 409s if
the operation isn't currently `waiting_hitl`.

**Implementation note for future maintainers:** `durable/pipeline.py` deliberately does not use
`from __future__ import annotations`. agent_framework's `@response_handler` decorator validates
its `WorkflowContext[...]` parameter by inspecting the *raw* annotation rather than resolving it
via `typing.get_type_hints`, so PEP 563 postponed evaluation makes that check see the literal
string `"WorkflowContext[PipelineState]"` and reject it. `@handler` doesn't have this issue - only
`@response_handler`, used by `SteeringGateExecutor.on_steering_decision`.

## Reconnecting mid-run

Checkpointing already meant a crashed *process* could resume an operation without redoing
work. What it didn't cover: a *client* that reconnects mid-run - a dropped Wi-Fi connection, a
closed tab reopened, a chat UI that retries after a timeout - previously only ever saw events
generated from the point of reconnection onward. Everything the workflow had already reported
before the drop (the "agent is working" status, the translation completing, an earlier steering
round) was gone for good, because SSE events were never anything but a live stream: produced
once, forwarded once, then lost.

`storage/metadata_store.py`'s `append_event`/`list_events` (SQLite table `operation_events`;
Table Storage table `operationevents`, `PartitionKey` = operation_id so replay is a single
partition query in RowKey/sequence order) fix that by durably logging every `StreamEvent` as
it's produced. `durable/engine.py`'s `_drive_and_persist` wraps `_drive_stream` for exactly this:

```python
async def _drive_and_persist(stream, store, operation_id, next_event):
    async for event in _drive_stream(stream, store, operation_id, next_event):
        await store.append_event(operation_id, event)
        yield event
```

`append_event` runs *before* the `yield` - so the event is captured even if there's nothing
left to actually deliver it to (the client is already gone; the generator won't be driven
further until something re-invokes it). This is the same "durability doesn't depend on anyone
being there to receive it" property checkpointing already has, applied to the event stream
instead of the workflow state.

Both entry points that can find an operation already under way now replay before they resume:

- `run_translation_operation`, when `existing["status"] == "in_progress"` (the same condition
  that already triggered a checkpoint resume) - this is the literal reconnect case: same
  `operation_id`, a fresh HTTP request, possibly a different tab or device.
- `respond_to_hitl` - always, unconditionally. A HITL pause is a hard stream boundary: the
  original SSE connection already ended cleanly the moment `set_waiting_on_hitl` ran, so
  *every* `/respond` call is a "reconnect" in the sense that matters here, even if it's the same
  browser tab that's been sitting on the `hitl_request` the whole time.

Both replay the same way: fetch `list_events(operation_id)`, yield each one back to the caller
verbatim (same `sequence`, same `stage`, same `data` - not re-derived, not summarized), then
drive the resumed/fresh workflow stream with `_sequencer(start=past_events[-1].sequence + 1)`
so the live events that follow continue the same numbering instead of restarting at 1. A
client that only ever looks at "did `sequence` go up" gets a single, gap-free, duplicate-free
timeline across however many reconnects happen - it never needs special-case logic for "was
this a fresh run or a resume".

What's deliberately *not* replayed: `_idempotent_replay` (the `existing["status"] == "completed"`
path) synthesizes a fresh status/artifact/completed trio on every call rather than reading from
the log - replaying the *original* run's full history for an operation that's long since
finished and might be replayed many times would grow the response for no benefit; a completed
operation only ever needs "here's your (possibly re-signed) download link", not a history
lesson. The event log's job is specifically the in-flight case.

**Retention**: event log rows are never deleted - same as the `operations` table itself, whose
rows are also never deleted, only status-flipped. Growth is bounded by the number of operations
ever created (each operation's own row count is bounded by its step count, a small constant),
not by the number of times it's reconnected to. If that ever becomes a real capacity concern,
the natural place to add cleanup is alongside `stale_operations.py`'s sweep, which already knows
which operations are being permanently retired.

## Observability

`observability.py` is a single module both apps call at import time
(`configure_observability()` + `configure_json_logging()`), and it's designed to degrade to
safe no-ops rather than fail when the optional `observability` extra isn't installed:

- **Tracing**: if `LDA_OTEL_EXPORTER` is `console` or `otlp`, an OpenTelemetry `TracerProvider`
  is configured with the corresponding exporter and handed to
  `agent_framework.observability.configure_otel_providers()`. The framework's own instrumentation
  then produces `workflow.run` spans (and per-executor spans) automatically - no custom span
  code was needed in `pipeline.py` or `engine.py` to get workflow-level tracing.
- **Metrics**: `metrics()` returns a memoized dict of Prometheus `Counter`/`Histogram`/`Gauge`
  instruments (`operations_started`, `operations_completed`, `operations_failed`,
  `operations_stopped`, `operation_duration_seconds`, `waiting_hitl_gauge`,
  `translation_duration_seconds`) if `prometheus_client` is installed and
  `LDA_METRICS_ENABLED=1`; otherwise every instrument is a `_NoopMetric` (a stand-in with
  `.inc`/`.dec`/`.observe`/`.set`/`.labels` that all do nothing), so instrumented code
  (`durable/engine.py`, `translator.py`) never has to branch on whether metrics are enabled.
  Both apps expose the Prometheus text format at `GET /metrics` via
  `metrics_endpoint_response()`.
- **Logs**: `operation_log_context(operation_id)` is a context manager backed by a
  `contextvars.ContextVar`; `JsonLogFormatter` reads it and stamps `operation_id` onto every log
  record emitted while a `run_translation_operation`/`respond_to_hitl` call is in flight, so logs
  from concurrent operations on the same process can be correlated without passing a logger
  around explicitly.

## Stale operation sweep

A crashed worker or an abandoned HITL prompt can leave an operation's metadata row stuck at
`in_progress` or `waiting_hitl` forever - nothing else in the system ever revisits it.
`stale_operations.sweep_stale_operations()` queries `list_stale_operations(older_than=...)`
(both metadata store backends implement this: SQLite filters
`status IN ('in_progress','waiting_hitl') AND updated_at <= ?`; Table Storage does the
equivalent) using a cutoff of `LDA_OPERATION_STALE_HOURS` (default 6) hours before "now", then
for each stale operation: deletes its hosted-agent scratch workspace file (if any) and marks it
`stopped`. It deliberately never looks at `completed`/`failed`/`stopped` rows, no matter how old,
so it can't undo a real result. Run it on a schedule (`python -m
long_duration_agent.stale_operations`, or an Azure Functions timer trigger) alongside the
existing artifact-TTL `cleanup.py` sweep - the two are independent: `cleanup.py` expires
*completed* artifacts in Blob Storage/metadata, this sweeps *stuck* operations that never
reached completion.

## Entra token hardening

`identity.py`'s `_resolve_entra()` validates more than "is this JWT signed by the right tenant":

- **Fails closed on misconfiguration.** An empty `ENTRA_AUDIENCE` raises `500` immediately
  rather than silently skipping audience validation - a missing config value can never
  degrade into an open endpoint.
- **Issuer check.** With `ENTRA_REQUIRE_ISSUER_MATCH=1` (default), the token's `iss` claim must
  match either the Entra v1 (`https://sts.windows.net/{tenant_id}/`) or v2
  (`https://login.microsoftonline.com/{tenant_id}/v2.0`) issuer format for the tenant the token
  claims (`tid`) - rejecting, for example, a validly-signed-but-wrong-issuer token from a
  different Entra product surface.
- **Scope/role checks.** `ENTRA_REQUIRED_SCOPE`/`ENTRA_REQUIRED_ROLE`, when set, reject (`403`)
  a token whose `scp`/`roles` claims don't include the required value - lets a deployment
  require a specific delegated scope or app role beyond "any valid token for this app".
- **JWKS fetch failures surface as `503`, not a crash** - `_get_signing_key()` wraps the
  `httpx.get` call in `try/except httpx.HTTPError`.

All of this is exercised in `tests/test_identity_entra.py` against real RSA-signed JWTs (a
locally generated keypair, a JWKS response built from it, `identity.httpx.get` monkeypatched to
serve that JWKS) - no live Entra tenant needed to test the validation logic itself.

## Key Vault secrets

There's no broker signing key anymore - SAS URLs are signed by Azure Storage itself (via a User
Delegation Key obtained through Managed Identity, or an account key for Azurite), not by this
app, so there's no app-level HMAC secret to protect for that path. The one optional secret left
is the Azure AI Content Safety API key (`content_safety.py`'s `"azure"` mode, if used with key
auth rather than Managed Identity). `secrets.py` centralizes sourcing it: if `LDA_KEY_VAULT_URL`
is set, `get_content_safety_api_key()` fetches `LDA_KEY_VAULT_CONTENT_SAFETY_KEY_SECRET_NAME`
(default `lda-content-safety-api-key`) from Key Vault via a synchronous
`azure.keyvault.secrets.SecretClient` + `DefaultAzureCredential` (sync is deliberate here,
matching the JWKS-fetch precedent in `identity.py` - this is called at most once per
`LDA_KEY_VAULT_CACHE_SECONDS` window, not from a hot per-request path that would need to avoid
blocking the event loop), caching the value in-process for that TTL (default 3600s). With
`LDA_KEY_VAULT_URL` unset, it falls back to `AZURE_CONTENT_SAFETY_API_KEY` directly - the
local-dev/demo path (or simply don't set an API key at all and use `DefaultAzureCredential`
against Content Safety instead, the same either-key-or-managed-identity pattern used
everywhere else in this codebase). `get_secret()` itself is generic - reused for any future
value that shouldn't live in a plain env var in production.

## Rate limiting

Starting a *new* translation operation costs a real resource per request (a model call).
`rate_limit.py` caps it per caller (`tenant_id:user_object_id`) with a plain in-memory sliding
window (`_SlidingWindowLimiter` - one `deque` of hit timestamps per key, pruned lazily on each
check) over a 60-second window: `LDA_RATE_LIMIT_INVOCATIONS_PER_MINUTE` (default 30); `0`
disables it, as does `LDA_RATE_LIMIT_ENABLED=0`.

The limiter is deliberately wired to skip resumed operations: `hosted_agent/app.py`'s `invoke()`
only calls `enforce_invocation_rate_limit()` when `check_operation_access()` couldn't find an
existing operation for that `operation_id` (`is_new_operation`). A dropped-connection reconnect
or a client retry replaying the same `operation_id` never repeats the translation call -
charging it against the limit would penalize exactly the reconnect story `durable/engine.py`'s
idempotent replay exists to support.

A caller over the limit gets `HTTPException(429, ...)` with a `Retry-After` header computed from
when its oldest in-window hit will age out; rejections also increment
`lda_invocation_rate_limited_total` (see Observability above).

This is intentionally a per-process, in-memory limiter - correct and sufficient for a single
hosted-agent instance, same scoping caveat as the default `file`/`sqlite` checkpoint/metadata
backends. A multi-instance deployment would need the counters in a shared store instead (Redis,
or the same Table Storage backend already used for checkpoints/metadata) so the limit applies
across replicas, not per-replica; `enforce_invocation_rate_limit()` is the only call site that
would need to change to make that swap.

Downloads are **not** rate limited by this app at all - since there's no broker, a download
never passes through it (see "Public storage + SAS" below); Blob Storage's own request-rate
behavior and the diagnostic logs described there are what govern and audit that path instead.

## Content safety guardrail

`content_safety.py`'s `check_content_safety()` runs once, in `ValidateExecutor`, on the original
English prompt - right after the character-limit check and before Translate, so a blocked prompt
never reaches the model or gets written to storage. Three modes
(`LDA_CONTENT_SAFETY_MODE`):

- `"off"` (default) - a no-op; unchanged behavior for the existing demo/test suite.
- `"blocklist"` - deterministic and offline: a case-insensitive substring match against
  `LDA_CONTENT_SAFETY_BLOCKLIST` (comma-separated terms). No external dependency, so this is
  what the always-run test coverage exercises.
- `"azure"` - calls Azure AI Content Safety's real async client
  (`azure.ai.contentsafety.aio.ContentSafetyClient.analyze_text`, `AnalyzeTextOptions(text=...)`)
  and blocks the prompt if any `TextCategoriesAnalysis.severity` returned is at or above
  `LDA_CONTENT_SAFETY_MAX_SEVERITY` (default 4). Azure's default "FourSeverityLevels" output type
  returns 0/2/4/6 per category (`Hate`/`SelfHarm`/`Sexual`/`Violence`) - 4 is Azure's own
  "Medium" cutoff. Auth is `AZURE_CONTENT_SAFETY_API_KEY` (an `AzureKeyCredential`) if set, else
  `DefaultAzureCredential` - the same either-key-or-managed-identity pattern as
  `AzureBlobStore`. Requires the `content-safety` extra (`azure-ai-contentsafety`); like the
  Foundry/OpenAI translator branches, the import is attempted lazily inside `_check_azure()` and
  turned into a clear `RuntimeError` naming the extra to install if it's missing, rather than a
  raw `ModuleNotFoundError` reaching the caller.

A blocked prompt raises `ContentSafetyBlockedError` (a `ValueError` subclass), which propagates
out of the executor exactly like `InputTooLargeError` already does - `durable/engine.py`'s
`_drive_stream()` has one generic `except Exception` handler that fails the operation and yields
`event: error` with the exception's message, so no special-casing was needed to wire this in.

This is checked only on the input prompt, not the translated output: the translator is
instructed to translate meaning faithfully, not add new content, so screening the input is the
meaningful checkpoint. A stricter deployment could call `check_content_safety()` a second time
on `state.spanish_text` in `TranslateExecutor`, using the exact same function.

## Tests for the real (non-stub) translation path

Every other test in this repo sets `LDA_USE_STUB_TRANSLATOR=1`, so `translator._model_translate`
- the code path that actually constructs a `FoundryChatClient`/`OpenAIChatCompletionClient` and
calls `get_response()` - had no coverage beyond "does the stub work". `tests/test_translator_model_path.py`
closes that gap:

- **Always runs, no extra needed**: `agent_framework.foundry`/`agent_framework.openai` are lazy
  shim modules inside `agent-framework-core` itself - `import agent_framework.foundry` always
  succeeds, but accessing `FoundryChatClient` on it raises `ModuleNotFoundError` at attribute-
  access time if the real `agent-framework-foundry` distribution isn't installed. Two tests
  exercise exactly that (real, always-reproducible in the base `[dev]` install) failure mode,
  verifying `translate_to_spanish` turns it into a `TranslationError` naming the extra to
  install - not a raw traceback.
- **Skipped without the `translate` extra, real otherwise**: the rest mock only
  `FoundryChatClient.__init__`/`OpenAIChatCompletionClient.__init__` (via
  `mock.patch.object(cls, "__init__", return_value=None)`) and `get_response` (via
  `new_callable=mock.AsyncMock`) - everything else is the real SDK class. This verifies, against
  the actual installed package: the Foundry client is constructed with `project_endpoint=`/
  `model=`/`credential=` (not, say, `endpoint=` or `deployment=`); the Azure OpenAI branch passes
  `azure_endpoint=`/`api_key=`/`model=`; the plain-OpenAI fallback omits `azure_endpoint`; a
  `get_response()` exception becomes a `TranslationError` with the original message; an
  all-whitespace response is rejected; and - a regression test for a bug already fixed once
  (see "Errors and fixes" history) - the messages sent are real `agent_framework.Message`
  objects with `role="system"`/`role="user"`, not raw dicts.
- Detection uses `importlib.util.find_spec("agent_framework_foundry")` /
  `find_spec("agent_framework_openai")` - the actual top-level distribution names (underscored,
  not the dotted `agent_framework.foundry` shim) - which return a clean `None` when not
  installed, unlike the namespace-package `find_spec` gotcha documented for `azure.*` packages
  elsewhere in this test suite.

## Public storage + SAS: how downloads work without a broker

An earlier version of this design kept the storage account private (public network access
disabled, reachable only via a private endpoint) and put an Artifact Broker API in front of it:
the chat UI got a broker-issued, HMAC-signed download token, and the broker re-verified the
token *and* re-checked artifact ownership against the metadata store before streaming any bytes
back. That gave a server-side re-check on every single download, at the cost of an extra service
to run, deploy, and keep patched, and an extra network hop (browser → broker → storage) for
every artifact fetch.

The current design removes the broker entirely and hands the chat UI a real, direct Azure Blob
SAS URL instead:

- **The storage account is reachable over the public internet** (`infra/storage-public.bicep`,
  `publicNetworkAccess: 'Enabled'`) - no private endpoint, no VNet integration required. Nothing
  about that makes the data public: anonymous blob/container access stays disabled
  (`allowBlobPublicAccess: false`, container `publicAccess: 'None'`); the only way to read a
  blob is with a valid, unexpired SAS token.
- **`storage/blob_store.py`'s `generate_download_url` mints that SAS directly** - no broker, no
  app-level signing scheme. For the `azure` backend it's a **User Delegation SAS**: the hosted
  agent's Managed Identity calls `BlobServiceClient.get_user_delegation_key()` (RBAC: Storage
  Blob Delegator) to get a key valid for about an hour, cached and reused across requests, and
  `generate_blob_sas(..., user_delegation_key=...)` signs each individual download link with it -
  no storage account key is ever used or needed. For the `azurite` backend (no real Azure
  Entra ID to delegate from), the same `generate_blob_sas` call is signed with Azurite's
  well-known account key instead - same code path, different credential.
- **The trade-off, explicitly**: a SAS URL is a bearer secret. Anyone holding a valid link can
  use it until it expires (`LDA_DOWNLOAD_SAS_TTL_MINUTES`, default 15) - there is no per-request
  server-side re-check of caller identity or artifact ownership the way the broker used to do.
  This is the same trade-off any presigned-URL design makes (S3 presigned URLs, GCS signed URLs);
  it's mitigated by keeping the TTL short and by the fact that a leaked link only exposes one
  artifact for a few minutes, not standing access to the account. If a deployment needs a
  server-side re-check on every download (e.g. to support revocation before expiry), that's
  exactly what reintroducing a broker buys back - this design deliberately trades that off for
  simplicity and one fewer service to operate.
- **Downloads are logged at the storage layer, not the app layer.** Since the browser talks to
  Blob Storage directly, the app never observes the actual SAS-authenticated `GET` - there is no
  code path to add an app-side download log to. `infra/storage-public.bicep` instead enables a
  diagnostic setting on the blob service (`StorageRead`/`StorageWrite`/`StorageDelete`
  categories) sent to a Log Analytics workspace, so every read is still auditable - just from
  Storage's own logs rather than this app's.
- **No SAS, no credential of any kind, is ever written to the metadata store.** SQLite here
  holds only `artifact_id → (tenant_id, user_object_id, blob_container, blob_name,
  display_name, size_bytes, created_at, expires_at, status)` - enough to resume operations and
  sweep expired artifacts. It is not an artifact catalogue (no list/browse UI is exposed to
  users, by design) and it is intentionally swappable for Table Storage/Cosmos DB in a
  multi-instance deployment (`storage/metadata_store.py` is a single narrow class; nothing
  outside it touches SQLite directly).
- **User isolation for who gets *handed* a link is still enforced server-side, from the
  validated token, never from the request body.** `identity.py` extracts `tid`/`oid` from a
  verified Entra JWT (or, for local dev only, a `X-Debug-User` header); `blob_name` is always
  built from that. What changed is what happens *after* the link is handed out: previously the
  broker re-checked on every fetch, now the SAS itself (plus its short TTL) is the only gate from
  that point on.

## TTL and cleanup

- **1-day artifact lifetime**, enforced two ways: the storage account's own lifecycle
  management policy (`infra/storage-public.bicep`, `daysAfterModificationGreaterThan: 1`)
  deletes the blob independent of the application; `cleanup.py` sweeps the metadata store so
  `run_translation_operation`'s idempotent replay reports the artifact expired (rather than
  minting a fresh SAS link for a blob that's already gone) even before the storage-side deletion
  runs.
- **The hosted agent's local copy is deleted immediately after a successful upload**
  (`CleanupLocalExecutor`), so the compute host's disk never accumulates artifacts across
  invocations - the local scratch directory is not the artifact store, only a workspace.

## Limits

- `LDA_MAX_INPUT_CHARS = 1,000,000` - checked before any translation call, so an oversized
  prompt fails fast without spending a model call or writing anything.
- `LDA_MAX_MARKDOWN_BYTES = 5 MiB` - a sanity cap on the rendered artifact, checked right
  before it's written to the workspace.

## Markdown format

```markdown
---
artifact_id: "<uuid>"
created_utc: "2026-07-29T10:15:22Z"
source_language: "en"
target_language: "es-ES"
---

# Original English Text

<verbatim prompt>

---

# Traducción al Español (España)

<model output, plain text, not re-parsed as Markdown>
```

The English text is preserved exactly as submitted. The Spanish text is the model's plain
output - it is not re-interpreted as Markdown, so a translated code fence or heading marker
can't accidentally restructure the document.

## Scaling beyond a single host

`durable/engine.py` defaults to `FileCheckpointStorage` + SQLite - fine for a demo or a single
hosted-agent instance, and still the fastest option for local development. The distributed
backends described below are implemented and tested, not just a suggested migration path:

1. **Distributed checkpoints** (done): `durable/table_checkpoint_storage.py` implements the
   `CheckpointStorage` protocol (`save`, `load`, `list_checkpoints`, `delete`, `get_latest`,
   `list_checkpoint_ids`) against Azure Table Storage/Azurite. Schema: `PartitionKey` =
   workflow name, `RowKey` = checkpoint id, with the checkpoint payload run through the
   framework's own `encode_checkpoint_value`/`decode_checkpoint_value` (the same
   allowlist-guarded pickle encoding `FileCheckpointStorage` uses) so nothing bespoke is
   invented for serialization. `get_latest` resolves the newest checkpoint across a workflow's
   partition by parsing each row's timestamp. Selected via `LDA_CHECKPOINT_BACKEND=azurite|azure`
   in `_get_checkpoint_storage()` (`durable/engine.py`) - see the Configuration section in
   `README.md`.
2. **Metadata store** (done): `storage/table_metadata_store.py` implements the same
   `MetadataStoreProtocol` as the SQLite `MetadataStore` (both now fully `async def`, with
   SQLite calls wrapped in `asyncio.to_thread` so the interface is uniformly non-blocking).
   Operations and artifacts each get a fixed `PartitionKey` (`"operation"` / `"artifact"`) for
   O(1) point lookups by `operation_id`/`artifact_id`; steering messages are partitioned by
   `operation_id` with a zero-padded-timestamp `RowKey` so `drain_steering_messages` gets FIFO
   order back within a single partition query. Selected via `LDA_METADATA_BACKEND=azurite|azure`
   in `get_metadata_store()` (`storage/metadata_store.py`).
   `tests/test_table_storage_backends.py` runs the full translation pipeline *and* a full
   steering/HITL pause-and-resume against both backends live against Azurite (skipped cleanly
   if `azure-data-tables` isn't installed or Azurite isn't reachable).
3. **Azure Functions Durable Task hosting** (reference implementation, not deployed):
   `agent-framework-durabletask` (published as a pre-release package alongside
   `agent-framework-azurefunctions`) runs the exact same `agent_framework.Workflow` as a Durable
   Task orchestration - the framework converts each `Executor`/edge into an activity/orchestrator
   pairing, giving you cross-process durability, automatic retries, and fan-out, on Azure's own
   durable execution engine rather than a single long-lived HTTP connection. See
   `azure_functions/function_app.py` (checked in, along with `host.json` and a
   `local.settings.json.example`):

   ```python
   from agent_framework_azurefunctions import AgentFunctionApp
   from long_duration_agent.durable.pipeline import build_workflow

   workflow = build_workflow(workflow_name="lda-translate", checkpoint_storage=None)
   app = AgentFunctionApp(workflow=workflow)  # registers HTTP + orchestrator/activity functions
   ```

   Adopting this changes the client contract: the synchronous SSE stream (`POST /invocations`
   held open for the whole operation) is replaced by an async HTTP 202 + status-polling pattern,
   and HITL responses are submitted via the `WorkflowHitlContext`-provided `respond`/`status`
   URLs instead of `/invocations/{operation_id}/respond`. This is reviewed but intentionally not
   live-tested in this repo (no Azure Functions Core Tools host in this environment) - treat
   `azure_functions/` as a vetted starting point, not a drop-in swap, and validate the HTTP
   contract change with the calling chat UI before switching a real deployment over to it.
