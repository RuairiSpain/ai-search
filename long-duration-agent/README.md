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
│   ├── identity.py              # caller identity: Entra JWT validation, or a dev header locally
│   ├── translator.py            # es-ES translation (Foundry/Azure OpenAI, or an offline stub)
│   ├── markdown_artifact.py     # bilingual Markdown rendering
│   ├── workspace.py              # hosted-agent local scratch filesystem ($HOME/artifacts equivalent)
│   ├── cleanup.py                # TTL sweeper (1 day) for expired artifacts
│   ├── durable/
│   │   ├── state.py               # PipelineState - the checkpointed message
│   │   ├── pipeline.py             # the 7-step MAF Workflow (one Executor per user-visible step)
│   │   └── engine.py               # runs/resumes the workflow, converts events to SSE, idempotency
│   ├── storage/
│   │   ├── blob_store.py           # LocalDiskBlobStore (demo) / AzureBlobStore (private, managed identity)
│   │   └── metadata_store.py        # SQLite: operation + artifact bookkeeping (no SAS stored, ever)
│   ├── broker/
│   │   ├── tokens.py                 # 15-minute signed download tokens (minted fresh every time)
│   │   └── api.py                     # Artifact Broker API: the only thing that can reach private storage
│   └── hosted_agent/
│       └── app.py                     # POST /invocations (SSE) - the Hosted Agent entrypoint
├── infra/storage-private.bicep    # private storage account + 1-day lifecycle policy + RBAC for the broker
├── tests/                           # pytest, no Azure credentials required
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
  `FOUNDRY_*`/`AZURE_OPENAI_*` for real es-ES translations).
- `LDA_STORAGE_BACKEND=local` - writes artifacts under `.data/blob-store` instead of Azure
  Blob Storage (set to `azure` and configure `AZURE_STORAGE_ACCOUNT_URL` for the private,
  production backend - see `infra/storage-private.bicep`).
- `LDA_IDENTITY_MODE=dev` - trusts an `X-Debug-User: <tenant_id>:<user_object_id>` header
  instead of validating a bearer token (set to `entra` in any real deployment).
- `LDA_ARTIFACT_TTL_HOURS=24`, `LDA_DOWNLOAD_TOKEN_TTL_MINUTES=15`,
  `LDA_MAX_INPUT_CHARS=1000000`.

## Production checklist

This is a working demo, not a finished production deployment. Before shipping:

- Set `LDA_IDENTITY_MODE=entra` and configure `ENTRA_TENANT_ID` / `ENTRA_AUDIENCE`.
- Deploy `infra/storage-private.bicep` (public network access disabled, private endpoint,
  1-day lifecycle policy) and set `LDA_STORAGE_BACKEND=azure`.
- Generate a real `LDA_BROKER_SIGNING_KEY` (`openssl rand -hex 32`) from Key Vault, not
  the repo default.
- Point `checkpoint_storage` (`durable/engine.py`) at a distributed backend, or host the
  same `Workflow` behind Azure Functions' Durable Task extension
  (`agent-framework-durabletask`) - see `docs/architecture.md`.
- Schedule `python -m long_duration_agent.cleanup` (or a Functions timer trigger) for the
  metadata-side TTL sweep.
