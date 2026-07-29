# long-duration-agent

A hosted Microsoft Agent Framework (MAF) agent that:

1. Takes an English prompt from a chat UI via a custom **Invocations** endpoint.
2. Streams status updates back over SSE while it works.
3. Translates the prompt into **Spain Spanish (es-ES)**.
4. Saves a bilingual Markdown artifact.
5. Waits 5s, announces the artifact was created, waits 2s.
6. Uploads the artifact to Blob Storage, deletes the local copy.
7. Mints a fresh, short-lived Blob **SAS** download link - straight from Storage, no broker or
   proxy in between - and sends it back to the chat UI.

The user can also **steer the agent while it's working**: `POST` additional text to
`/invocations/{operation_id}/steer` at any point before the artifact reaches Blob Storage. The
workflow's steering checkpoint concatenates it with the current prompt, asks the user to
confirm via a human-in-the-loop (HITL) request (`event: hitl_request`, showing the full
combined text), and - depending on the answer sent to `/invocations/{operation_id}/respond`
(`yes` / `edit` / `stop`) - either re-translates the combined text, re-translates a fully
edited replacement, or cancels the operation and cleans up. See "Steering while the agent is
working" in `docs/architecture.md` for the full flow.

The pipeline is a durable, checkpointed [MAF Workflow](https://github.com/microsoft/agent-framework)
(`src/long_duration_agent/durable/pipeline.py`) - if the process crashes between any two
steps, resubmitting the same `operation_id` resumes from the last completed step instead of
starting over. See `docs/architecture.md` for the full design and the production upgrade path
(Azure Functions + `agent-framework-durabletask`).

## Project layout

```text
long-duration-agent/
├── src/long_duration_agent/
│   ├── config.py              # env-driven settings
│   ├── models.py               # request/event/artifact pydantic models
│   ├── limits.py                # 1,000,000-character input cap, artifact size cap
│   ├── identity.py              # caller identity: Entra JWT validation (audience/issuer/scope/role checks), or a dev header locally
│   ├── secrets.py                # content-safety API key from Key Vault (cached) or an env var
│   ├── observability.py          # OpenTelemetry tracing setup, Prometheus metrics, correlated JSON logs
│   ├── rate_limit.py             # per-caller sliding-window limit on new operations
│   ├── content_safety.py         # optional guardrail on the prompt before Translate: off/blocklist/Azure AI Content Safety
│   ├── translator.py            # es-ES translation (Foundry/Azure OpenAI, or an offline stub)
│   ├── markdown_artifact.py     # bilingual Markdown rendering
│   ├── workspace.py              # hosted-agent local scratch filesystem ($HOME/artifacts equivalent)
│   ├── cleanup.py                # TTL sweeper (1 day) for expired artifacts
│   ├── stale_operations.py       # sweeper for operations stuck in_progress/waiting_hitl (6h default)
│   ├── durable/
│   │   ├── state.py               # PipelineState - the checkpointed message
│   │   ├── pipeline.py             # the 7-step MAF Workflow (one Executor per user-visible step)
│   │   ├── engine.py               # runs/resumes the workflow, converts events to SSE, idempotency
│   │   └── table_checkpoint_storage.py  # Table Storage CheckpointStorage (multi-instance)
│   ├── storage/
│   │   ├── blob_store.py           # LocalDiskBlobStore (demo) / AzureBlobStore (public+SAS, managed identity) - generate_download_url mints the SAS link directly, no broker
│   │   ├── metadata_store.py        # SQLite: operation + artifact bookkeeping (no SAS stored, ever)
│   │   └── table_metadata_store.py   # Table Storage equivalent (multi-instance)
│   └── hosted_agent/
│       └── app.py                     # POST /invocations (SSE), /steer, /respond, /metrics - the Hosted Agent entrypoint (the only app - no separate broker service)
├── infra/storage-public.bicep     # public-network, SAS-gated storage account + 1-day lifecycle policy + Log Analytics read-logging + RBAC for the hosted agent
├── azure_functions/                 # reference: hosting the same Workflow on Azure Functions' Durable Task engine
├── tests/                           # pytest, no Azure credentials required (Table Storage/Key Vault/Azurite tests skip cleanly if unavailable)
└── docs/
    ├── architecture.md              # full design, rationale, production upgrade path
    └── chat-integrations.md         # Teams / Copilot Studio / M365 Copilot / OBO notes
```

## Run it locally (no Azure required)

```bash
cd long-duration-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # defaults already run fully offline: stub translator, local-disk storage

pytest -q
```

Start the hosted agent (there's only one app - no separate broker service):

```bash
uvicorn long_duration_agent.hosted_agent.app:app --port 8080 &
```

Call it (streams SSE; `X-Debug-User` stands in for a validated Entra token locally - see
`LDA_IDENTITY_MODE` below):

```bash
curl -N -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -H "X-Debug-User: contoso-tenant:alice-object-id" \
  -d '{"prompt": "Hello, how are you today?", "operation_id": "demo-1"}'
```

The final `event: artifact` line contains a `download_url`. With the default `local` storage
backend it's a `file://` URI onto the demo's on-disk stand-in (not fetchable remotely - it's a
test/demo convenience, see `storage/blob_store.py`); with `LDA_STORAGE_BACKEND=azurite` or
`azure` it's a real, signed Blob SAS URL any HTTP client (`curl`, a browser) can fetch directly,
with no further authentication and no broker in between.

## Configuration

See `.env.example` for the full list. The defaults run the whole pipeline offline:

- `LDA_USE_STUB_TRANSLATOR=1` - skips the model call (set to `0` and configure
  `FOUNDRY_*`/`AZURE_OPENAI_*` for real es-ES translations - if `FOUNDRY_PROJECT_ENDPOINT` is
  set, Foundry is used; otherwise Azure OpenAI/OpenAI. Also run
  `pip install -e ".[translate]"` first - `agent-framework-openai`/`agent-framework-foundry`
  aren't needed for the offline stub, so they're an optional extra, not a base dependency).
- `LDA_STORAGE_BACKEND=local` - writes artifacts under `.data/blob-store`, no dependencies.
  Set to `azurite` to exercise the real `azure-storage-blob` SDK - including real SAS
  generation - against a local
  [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) emulator
  instead (`docker run -p 10000:10000 mcr.microsoft.com/azure-storage/azurite` or
  `npx azurite-blob --blobHost 0.0.0.0`) - a much closer rehearsal of production than the
  local-disk stand-in, still with zero real Azure resources. Set to `azure` and configure
  `AZURE_STORAGE_ACCOUNT_URL` for the production backend - see "Public storage + SAS" below and
  `infra/storage-public.bicep`.
- `LDA_IDENTITY_MODE=dev` - trusts an `X-Debug-User: <tenant_id>:<user_object_id>` header
  instead of validating a bearer token (set to `entra` in any real deployment).
- `LDA_ARTIFACT_TTL_HOURS=24`, `LDA_DOWNLOAD_SAS_TTL_MINUTES=15`,
  `LDA_MAX_INPUT_CHARS=1000000`.

### Public storage + SAS (no broker)

There is no broker or proxy service: `storage/blob_store.py`'s `generate_download_url` mints a
real, time-limited Azure Blob SAS URL directly, and the chat UI's browser fetches the blob from
Storage itself. The storage account is reachable over the public internet (no private endpoint
needed), but anonymous blob access stays disabled - security comes entirely from the SAS's
signature and expiry (`LDA_DOWNLOAD_SAS_TTL_MINUTES`, default 15), not network isolation.
Anyone holding a valid link can use it until it expires; there's no server-side re-check on
every download the way a broker would do, so keep the TTL short.

- `azurite` backend: SAS is signed with Azurite's well-known account key (not a secret - see
  `.env.example`).
- `azure` backend: SAS is signed with a **User Delegation Key** (`BlobServiceClient
  .get_user_delegation_key`, cached in-process for ~1 hour) obtained via the hosted agent's own
  Managed Identity - no storage account key is ever used or needed. The identity needs two RBAC
  roles on the storage account: **Storage Blob Data Contributor** (upload/delete) and **Storage
  Blob Delegator** (mint the delegation key) - both wired up by `infra/storage-public.bicep`.

**Read logging**: since the app never sees the actual SAS-authenticated download (the browser
talks to Storage directly), reads are logged at the storage layer instead - `infra/storage-
public.bicep` enables a diagnostic setting on the blob service (`StorageRead`/`StorageWrite`/
`StorageDelete` categories) sent to a Log Analytics workspace. Query it for who downloaded what
and when; there is no equivalent app-side log for this specific event.

### Distributed checkpoint and metadata backends (multi-instance)

The defaults (`LDA_CHECKPOINT_BACKEND=file`, `LDA_METADATA_BACKEND=sqlite`) are single-instance
only - fine for local runs, wrong for anything horizontally scaled, since a resumed operation
has to land back on the same process that paused it. Set both to `azurite` (against the same
local emulator used for blob storage) or `azure` (against a real Storage account) to move
checkpoints and operation/artifact/steering bookkeeping into Azure Table Storage, shared by every
instance:

- `LDA_CHECKPOINT_BACKEND=azurite|azure`, `LDA_CHECKPOINT_TABLE_NAME=workflowcheckpoints`
- `LDA_METADATA_BACKEND=azurite|azure`, `LDA_OPERATIONS_TABLE_NAME=operations`,
  `LDA_ARTIFACTS_TABLE_NAME=artifacts`, `LDA_STEERING_TABLE_NAME=steeringmessages`
- `AZURE_TABLE_ACCOUNT_URL` - required when either backend is `azure` (uses
  `DefaultAzureCredential`, same as blob storage); with `azurite` both reuse
  `AZURITE_CONNECTION_STRING`.

Requires the `production` extra (`pip install -e ".[production]"`, adds `azure-data-tables`).
`tests/test_table_storage_backends.py` exercises both backends end-to-end (including a full
steering/HITL resume) but skips cleanly if the extra isn't installed or Azurite isn't reachable.

### Observability

- `LDA_OTEL_EXPORTER=none|console|otlp` - `none` (default) does nothing; `console` prints spans
  for local debugging; `otlp` exports to `LDA_OTEL_ENDPOINT` (e.g. an Azure Monitor/App Insights
  OTLP ingestion endpoint or a local collector). `LDA_SERVICE_NAME` sets the resource
  `service.name`. The Workflow's own `agent_framework.observability` instrumentation produces
  `workflow.run` spans automatically once a provider is configured - no custom spans needed.
- `LDA_METRICS_ENABLED=1` (default) exposes Prometheus metrics at `/metrics` (operation
  counts/duration, HITL-wait gauge, translation duration, rate-limit rejections) - degrades to
  no-op automatically if `prometheus-client` isn't installed.
- Logs are correlated JSON (`operation_id` on every line inside a running operation) once
  `configure_json_logging()` runs, which the app does at import time.
- Requires the `observability` extra (`pip install -e ".[observability]"`) for real OTEL export
  and real Prometheus metrics; without it, both degrade to safe no-ops so the app still runs.
- Download reads aren't covered by this app's own metrics/logs - they never reach this app (see
  "Public storage + SAS" above); that's what the storage account's own diagnostic logs are for.

### Stale operation sweep

Operations that get stuck `in_progress` or `waiting_hitl` (crashed worker, abandoned HITL
prompt) are never automatically retried - `LDA_OPERATION_STALE_HOURS=6` (default) controls how
old is "stuck". Run `python -m long_duration_agent.stale_operations` on a schedule (cron, or a
Functions timer trigger) to mark them `stopped` and clean up their workspace scratch files; it
never touches `completed`/`failed`/`stopped` operations regardless of age.

### Entra hardening

Beyond `LDA_IDENTITY_MODE=entra` + `ENTRA_TENANT_ID` / `ENTRA_AUDIENCE`, validation now also
checks:

- `ENTRA_REQUIRE_ISSUER_MATCH=1` (default) - rejects tokens whose `iss` claim doesn't match the
  expected v1 (`https://sts.windows.net/{tenant}/`) or v2
  (`https://login.microsoftonline.com/{tenant}/v2.0`) issuer for the resolved tenant.
- `ENTRA_REQUIRED_SCOPE` / `ENTRA_REQUIRED_ROLE` - optional; when set, a token missing that
  `scp` entry or `roles` entry is rejected with `403`.
- A missing `ENTRA_AUDIENCE` now fails closed (`500`) instead of silently accepting unverified
  tokens.

### Key Vault secrets

`LDA_KEY_VAULT_URL` - when set, the Content Safety API key (see below) is fetched from
`LDA_KEY_VAULT_CONTENT_SAFETY_KEY_SECRET_NAME` (default `lda-content-safety-api-key`) via
`DefaultAzureCredential`, cached in-process for `LDA_KEY_VAULT_CACHE_SECONDS` (default 3600).
Leave `LDA_KEY_VAULT_URL` empty to fall back to `AZURE_CONTENT_SAFETY_API_KEY` directly
(local/dev, or when using Managed Identity auth for Content Safety instead of a key). Requires
the `production` extra (`azure-keyvault-secrets`); `tests/test_secrets.py`'s Key-Vault-backed
cases skip cleanly without it. (There's no broker signing key to source anymore - SAS URLs are
signed by Azure Storage itself, not by this app.)

### Rate limiting

`LDA_RATE_LIMIT_ENABLED=1` (default) caps genuinely *new* `POST /invocations` per caller
(`tenant_id` + `user_object_id`) using an in-memory sliding window over 60 seconds -
`LDA_RATE_LIMIT_INVOCATIONS_PER_MINUTE=30` (never applied to a resumed/replayed
`operation_id` - see `rate_limit.py`). Set to `0` to disable. A caller over the limit gets `429`
with a `Retry-After` header; rejections also increment `lda_invocation_rate_limited_total` on
`/metrics`. This is per-process - correct for a single instance, but a multi-instance deployment
needs a shared store (e.g. the same Table Storage already used for checkpoints/metadata) for the
limit to apply across replicas; see `docs/architecture.md`. Downloads aren't rate limited here -
they never pass through this app (see "Public storage + SAS" above); use Blob Storage's own
throttling for that.

### Content safety guardrail

`LDA_CONTENT_SAFETY_MODE=off` (default, unchanged demo behavior) checks nothing.
`LDA_CONTENT_SAFETY_MODE=blocklist` rejects a prompt containing any comma-separated term from
`LDA_CONTENT_SAFETY_BLOCKLIST`, case-insensitively - no dependency, good for CI/tests.
`LDA_CONTENT_SAFETY_MODE=azure` calls Azure AI Content Safety's `analyze_text` and rejects the
prompt if any category's severity is at or above `LDA_CONTENT_SAFETY_MAX_SEVERITY` (default 4,
Azure's own "Medium" threshold); requires `AZURE_CONTENT_SAFETY_ENDPOINT` and either
`AZURE_CONTENT_SAFETY_API_KEY` or a Managed Identity, plus the `content-safety` extra
(`pip install -e ".[content-safety]"`). Checked once, on the English prompt, in `ValidateExecutor`
- before any translation call or storage write; a blocked prompt surfaces as a normal
`event: error` on the SSE stream, the same way an oversized prompt does.

## Production checklist

This is a working demo, but most of the production hardening below is now implemented -
what's left is largely deployment/ops, not code:

- [x] Entra hardening - `LDA_IDENTITY_MODE=entra`, audience fails closed, issuer/scope/role
  checks (`identity.py`, `docs/architecture.md`).
- [x] Distributed checkpoint + metadata store - Table Storage backends for both, so a resumed
  operation doesn't need to land back on the same instance (`durable/table_checkpoint_storage.py`,
  `storage/table_metadata_store.py`).
- [x] Key Vault secret sourcing for the Content Safety API key (`secrets.py`), cached with a TTL.
- [x] Stale/orphaned operation sweep (`stale_operations.py`) - schedule it, don't just have it.
- [x] Observability - OpenTelemetry tracing, Prometheus `/metrics`, correlated JSON logs
  (`observability.py`).
- [x] Rate limiting on new operations (`rate_limit.py`) - in-memory/per-process; swap for a
  shared store before running more than one instance.
- [x] Content safety guardrail on the prompt before translation (`content_safety.py`) - `off` by
  default; turn on `blocklist` or `azure` before accepting untrusted input.
- [x] Tests for the real (non-stub) translation path (`tests/test_translator_model_path.py`) -
  mocked chat clients verifying request/response wiring against the actual SDK, not just the
  offline stub.
- [x] No broker/proxy: downloads are real, direct Blob SAS URLs
  (`storage/blob_store.py`'s `generate_download_url`) signed by a User Delegation Key via
  Managed Identity - never a storage account key, never a hand-rolled signing scheme.
- [x] Public-network storage account with SAS-gated (not anonymous) blob access, plus
  Log Analytics diagnostic logging of every blob read/write/delete (`infra/storage-public.bicep`)
  - since there's no broker to log downloads at the app layer, this is the audit trail.
- [ ] Deploy `infra/storage-public.bicep` and set `LDA_STORAGE_BACKEND=azure` +
  `AZURE_STORAGE_ACCOUNT_URL`.
- [ ] Schedule `python -m long_duration_agent.cleanup` and
  `python -m long_duration_agent.stale_operations` (cron, or Functions timer triggers).
- [ ] `azure_functions/` is a reviewed hosting reference for Azure Functions' Durable Task
  extension (`agent-framework-durabletask`), not a deployed/live-tested target yet - see
  `docs/architecture.md` before relying on it.
- [ ] Consider whether `LDA_DOWNLOAD_SAS_TTL_MINUTES` (default 15) is short enough for your
  threat model - a leaked SAS URL is usable by anyone until it expires, with no server-side
  re-check the way a broker would provide.
