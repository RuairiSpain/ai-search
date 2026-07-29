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
  ├─ Validate          → reject if prompt > 1,000,000 characters
  ├─ SSE status: "The agent is working..."
  ├─ Translate          → es-ES, via Foundry/Azure OpenAI chat client
  ├─ SSE status: "The text has been translated."
  ├─ Save Markdown      → hosted-agent local scratch workspace (temporary only)
  ├─ wait 5s
  ├─ SSE status: "The artifact was created successfully."
  ├─ Steering gate       → any queued steering messages? (see "Steering while the agent is
  │                         working" below) - if yes, HITL confirm, then loop back to Validate
  ├─ wait 2s
  ├─ Upload             → private Blob Storage, users/<tenant>/<object-id>/<artifact-id>.md
  ├─ SSE status: "The artifact was saved to secure storage."
  ├─ Delete local copy  → cleans up the hosted-agent's scratch file
  ├─ Mint download link → Artifact Broker API, 15-minute signed token, freshly issued
  └─ SSE artifact: { artifact_id, download_url, expires_at }
  │
  ▼
Private Storage Account (infra/storage-private.bicep)
  - public network access disabled
  - 1-day blob lifecycle policy
  - only reachable by the Artifact Broker API's managed identity
  │
  ▼
User's browser ── GET {download_url} ──▶ Artifact Broker API ──▶ streams the blob
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
- `azure` - `AzureBlobStore` against a real, private storage account via `account_url` +
  `DefaultAzureCredential` (Managed Identity in production, `az login` for a developer).

Azurite's connection string uses its own published, well-known development account key -
not a secret, and not usable against any real Azure account.

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

## Storage and identity: what changed from the initial design

The first draft of this design considered handing the browser a raw Azure Blob SAS URL.
That does not work once the storage account has public network access disabled (the
explicit requirement here) - there is no public endpoint for a SAS URL to point at. The
corrected design:

- **The storage account is never reachable from outside the VNet.** Only the Artifact
  Broker API's managed identity (RBAC: Storage Blob Data Contributor) can read/write/delete
  blobs.
- **The chat UI gets a broker-issued download link, not a Blob SAS.** `broker/tokens.py`
  mints an HMAC-signed, single-artifact, 15-minute token on every request - never persisted,
  never reused. The broker (`broker/api.py`) verifies the token *and* re-checks ownership
  against the authoritative metadata record before streaming anything, so a leaked-but-valid
  token still can't read another user's artifact.
- **No SAS, no credential of any kind, is ever written to the metadata store.** SQLite here
  holds only `artifact_id → (tenant_id, user_object_id, blob_container, blob_name,
  display_name, size_bytes, created_at, expires_at, status)` - enough to resume operations,
  enforce ownership, and sweep expired artifacts. It is not an artifact catalogue (no
  list/browse UI is exposed to users, by design) and it is intentionally swappable for Table
  Storage/Cosmos DB in a multi-instance deployment (`storage/metadata_store.py` is a single
  narrow class; nothing outside it touches SQLite directly).
- **User isolation is enforced server-side, from the validated token, never from the request
  body.** `identity.py` extracts `tid`/`oid` from a verified Entra JWT (or, for local dev
  only, a `X-Debug-User` header); `blob_name` is always built from that, and the broker
  double-checks the artifact record's owner against the caller on every download.

## TTL and cleanup

- **1-day artifact lifetime**, enforced two ways: the storage account's own lifecycle
  management policy (`infra/storage-private.bicep`, `daysAfterModificationGreaterThan: 1`)
  deletes the blob independent of the application; `cleanup.py` sweeps the metadata store so
  expired records read as gone (the broker 404s) even before the storage-side deletion runs.
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

`durable/engine.py` uses `FileCheckpointStorage` and an in-process asyncio loop - fine for a
demo or a single hosted-agent instance. To scale to a real pipeline without changing
`pipeline.py`:

1. **Distributed checkpoints**: implement the `CheckpointStorage` protocol (`save`, `load`,
   `list_checkpoints`, `delete`, `get_latest`, `list_checkpoint_ids`) against Cosmos DB or
   Table Storage, and pass that instance instead of `FileCheckpointStorage`.
2. **Azure Functions Durable Task hosting**: `agent-framework-durabletask` (published as a
   pre-release package alongside `agent-framework-azurefunctions`) runs the exact same
   `agent_framework.Workflow` as a Durable Task orchestration - the framework converts each
   `Executor`/edge into an activity/orchestrator pairing, giving you cross-process durability,
   automatic retries, and fan-out, on Azure's own durable execution engine rather than a
   single long-lived HTTP connection. A production `function_app.py` looks like:

   ```python
   import azure.functions as func
   from agent_framework.azure import AgentFunctionApp
   from long_duration_agent.durable.pipeline import build_workflow

   workflow = build_workflow(workflow_name="lda-translate", checkpoint_storage=None)
   app = AgentFunctionApp(workflows=[workflow])  # registers HTTP + orchestrator/activity functions
   ```

   This is intentionally not wired into the demo (it requires the Azure Functions Core Tools
   host and a Durable Task storage backend to run), but no change to the pipeline's step
   logic is needed to adopt it - only the hosting layer changes.
3. **Metadata store**: swap `storage/metadata_store.MetadataStore` for a Table
   Storage/Cosmos-backed implementation of the same interface once you're running more than
   one instance.
