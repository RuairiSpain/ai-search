# long-duration-agent

A hosted Microsoft Agent Framework (MAF) agent that:

1. Takes an English prompt from a chat UI via a custom **Invocations** endpoint.
2. Streams status updates back over SSE while it works.
3. Translates the prompt into **Spain Spanish (es-ES)**.
4. Saves a bilingual Markdown artifact.
5. Waits 5s, announces the artifact was created, waits 2s.
6. Uploads the artifact to **private** Blob Storage, deletes the local copy.
7. Mints a fresh 15-minute download link (via an Artifact Broker API, since the storage
   account has no public endpoint) and sends it back to the chat UI.

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
│   ├── secrets.py                # broker signing key from Key Vault (cached) or an env var
│   ├── observability.py          # OpenTelemetry tracing setup, Prometheus metrics, correlated JSON logs
│   ├── rate_limit.py             # per-caller sliding-window limits: new operations, downloads
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
│   │   ├── blob_store.py           # LocalDiskBlobStore (demo) / AzureBlobStore (private, managed identity)
│   │   ├── metadata_store.py        # SQLite: operation + artifact bookkeeping (no SAS stored, ever)
│   │   └── table_metadata_store.py   # Table Storage equivalent (multi-instance)
│   ├── broker/
│   │   ├── tokens.py                 # 15-minute signed download tokens (minted fresh every time)
│   │   └── api.py                     # Artifact Broker API: the only thing that can reach private storage
│   └── hosted_agent/
│       └── app.py                     # POST /invocations (SSE), /steer, /respond, /metrics - the Hosted Agent entrypoint
├── infra/storage-private.bicep    # private storage account + 1-day lifecycle policy + RBAC for the broker
├── azure_functions/                 # reference: hosting the same Workflow on Azure Functions' Durable Task engine
├── tests/                           # pytest, no Azure credentials required (Table Storage/Key Vault tests skip cleanly if unavailable)
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

Start both services:

```bash
uvicorn long_duration_agent.hosted_agent.app:app --port 8080 &
uvicorn long_duration_agent.broker.api:app --port 8081 &
```

Call the hosted agent (streams SSE; `X-Debug-User` stands in for a validated Entra token
locally - see `LDA_IDENTITY_MODE` below):

```bash
curl -N -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -H "X-Debug-User: contoso-tenant:alice-object-id" \
  -d '{"prompt": "Hello, how are you today?", "operation_id": "demo-1"}'
```

The final `event: artifact` line contains a `download_url` pointing at the broker on
`:8081`; `curl` (with the same `X-Debug-User` header) or a browser can fetch it directly.

## Configuration

See `.env.example` for the full list. The defaults run the whole pipeline offline:

- `LDA_USE_STUB_TRANSLATOR=1` - skips the model call (set to `0` and configure
  `FOUNDRY_*`/`AZURE_OPENAI_*` for real es-ES translations - if `FOUNDRY_PROJECT_ENDPOINT` is
  set, Foundry is used; otherwise Azure OpenAI/OpenAI. Also run
  `pip install -e ".[translate]"` first - `agent-framework-openai`/`agent-framework-foundry`
  aren't needed for the offline stub, so they're an optional extra, not a base dependency).
- `LDA_STORAGE_BACKEND=local` - writes artifacts under `.data/blob-store`, no dependencies.
  Set to `azurite` to exercise the real `azure-storage-blob` SDK against a local
  [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) emulator
  instead (`docker run -p 10000:10000 mcr.microsoft.com/azure-storage/azurite` or
  `npx azurite-blob --blobHost 0.0.0.0`) - a much closer rehearsal of production than the
  local-disk stand-in, still with zero real Azure resources. Set to `azure` and configure
  `AZURE_STORAGE_ACCOUNT_URL` for the private, production backend - see
  `infra/storage-private.bicep`.
- `LDA_IDENTITY_MODE=dev` - trusts an `X-Debug-User: <tenant_id>:<user_object_id>` header
  instead of validating a bearer token (set to `entra` in any real deployment).
- `LDA_ARTIFACT_TTL_HOURS=24`, `LDA_DOWNLOAD_TOKEN_TTL_MINUTES=15`,
  `LDA_MAX_INPUT_CHARS=1000000`.

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
- `LDA_METRICS_ENABLED=1` (default) exposes Prometheus metrics at `/metrics` on both the hosted
  agent and broker (operation counts/duration, HITL-wait gauge, translation duration) - degrades
  to no-op automatically if `prometheus-client` isn't installed.
- Logs are correlated JSON (`operation_id` on every line inside a running operation) once
  `configure_json_logging()` runs, which both apps do at import time.
- Requires the `observability` extra (`pip install -e ".[observability]"`) for real OTEL export
  and real Prometheus metrics; without it, both degrade to safe no-ops so the app still runs.

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

`LDA_KEY_VAULT_URL` - when set, the broker's HMAC signing key is fetched from
`LDA_KEY_VAULT_SIGNING_KEY_SECRET_NAME` (default `lda-broker-signing-key`) via
`DefaultAzureCredential`, cached in-process for `LDA_KEY_VAULT_CACHE_SECONDS` (default 3600).
Leave `LDA_KEY_VAULT_URL` empty to fall back to `LDA_BROKER_SIGNING_KEY` directly (local/dev
only). Requires the `production` extra (`azure-keyvault-secrets`); `tests/test_secrets.py`'s
Key-Vault-backed cases skip cleanly without it.

### Rate limiting

`LDA_RATE_LIMIT_ENABLED=1` (default) caps two calls per caller (`tenant_id` + `user_object_id`),
using an in-memory sliding window over 60 seconds - `LDA_RATE_LIMIT_INVOCATIONS_PER_MINUTE=30`
for genuinely *new* `POST /invocations` (never a resumed/replayed `operation_id` - see
`rate_limit.py`) and `LDA_RATE_LIMIT_DOWNLOADS_PER_MINUTE=60` for
`GET /artifacts/{id}/download`. Either limit set to `0` disables just that limiter. A caller over
the limit gets `429` with a `Retry-After` header; rejections also increment
`lda_invocation_rate_limited_total`/`lda_download_rate_limited_total` on `/metrics`. This is
per-process - correct for a single instance, but a multi-instance deployment needs a shared
store (e.g. the same Table Storage already used for checkpoints/metadata) for the limit to apply
across replicas; see `docs/architecture.md`.

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
- [x] Key Vault secret sourcing for the broker signing key (`secrets.py`), cached with a TTL.
- [x] Stale/orphaned operation sweep (`stale_operations.py`) - schedule it, don't just have it.
- [x] Observability - OpenTelemetry tracing, Prometheus `/metrics`, correlated JSON logs
  (`observability.py`).
- [x] Rate limiting on new operations and downloads (`rate_limit.py`) - in-memory/per-process;
  swap for a shared store before running more than one instance.
- [x] Content safety guardrail on the prompt before translation (`content_safety.py`) - `off` by
  default; turn on `blocklist` or `azure` before accepting untrusted input.
- [x] Tests for the real (non-stub) translation path (`tests/test_translator_model_path.py`) -
  mocked chat clients verifying request/response wiring against the actual SDK, not just the
  offline stub.
- [ ] Deploy `infra/storage-private.bicep` (public network access disabled, private endpoint,
  1-day lifecycle policy) and set `LDA_STORAGE_BACKEND=azure`.
- [ ] Generate a real `LDA_BROKER_SIGNING_KEY` (`openssl rand -hex 32`) and store it in Key
  Vault rather than relying on the env fallback.
- [ ] Schedule `python -m long_duration_agent.cleanup` and
  `python -m long_duration_agent.stale_operations` (cron, or Functions timer triggers).
- [ ] `azure_functions/` is a reviewed hosting reference for Azure Functions' Durable Task
  extension (`agent-framework-durabletask`), not a deployed/live-tested target yet - see
  `docs/architecture.md` before relying on it.
