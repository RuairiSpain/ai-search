# Postgres Schema

Beyond what `a2a-sdk`'s `DatabaseTaskStore` creates. Prefix `gw_` to avoid
collision with SDK-managed tables. T3 uses a **separate schema**
(`agentsrv.*`) on the same server — see tier3 doc §6.3 — never the same
table namespaced by app name.

```sql
-- Per-user, per-app conversation continuity.
CREATE TABLE gw_context (
    context_id        TEXT PRIMARY KEY,
    app               TEXT NOT NULL,
    principal_subject TEXT NOT NULL,
    session_id        TEXT,          -- T2 agent_session_id
    conversation_id   TEXT,          -- T2 Foundry conversation
    instance_url      TEXT,          -- T3 affinity pin (BYO-compute only; unused with DTS)
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- THE tier-2 safety net. The platform will not fence delegated users from
-- each other; this constraint is what actually prevents cross-user sandbox
-- reuse. Do not drop it for a "shared session" feature without a design review.
CREATE UNIQUE INDEX gw_context_session_owner
    ON gw_context (app, session_id)
    WHERE session_id IS NOT NULL;

CREATE INDEX gw_context_by_principal
    ON gw_context (app, principal_subject, last_seen_at DESC);

CREATE TABLE gw_task (
    task_id           TEXT PRIMARY KEY,
    context_id        TEXT NOT NULL REFERENCES gw_context(context_id),
    app               TEXT NOT NULL,
    tier              TEXT NOT NULL CHECK (tier IN ('t2','t3')),
    state             TEXT NOT NULL,
    run_id            TEXT,
    current_message_id TEXT,        -- points into gw_message; NULL when the
                                     -- current status has no associated message
    last_sequence     INT  NOT NULL DEFAULT 0,
    -- wedged-task detection: a reaper fails tasks whose lease has lapsed
    lease_expires_at  TIMESTAMPTZ,
    heartbeat_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Reaper must exclude 'input-required' — a multi-day HITL approval is not
-- wedged. Lease duration is also per-tier: T3 legitimately runs for days,
-- so one global timeout kills live work (see 08-open-items-and-experiments.md).
CREATE INDEX gw_task_reaper
    ON gw_task (lease_expires_at)
    WHERE state IN ('submitted','working');

-- Append-only. Cross-replica fan-in: a callback landing on worker 1 reaches
-- an SSE stream held by worker 2 via LISTEN/NOTIFY on insert.
CREATE TABLE gw_event (
    task_id     TEXT NOT NULL REFERENCES gw_task(task_id),
    sequence    INT  NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('status','artifact')),
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, sequence)   -- idempotent: duplicate callbacks no-op
);

-- Artifacts live in blob storage; this table is the index and the authz record.
-- The upstream reference is TRANSIENT (a code interpreter container dies in
-- ~1 hour) and is never the canonical location. See 07-artifacts-and-code-interpreter.md.
CREATE TABLE gw_artifact (
    artifact_id  TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL REFERENCES gw_task(task_id),
    name         TEXT NOT NULL,
    mime         TEXT NOT NULL,
    -- artifacts/{app}/{principal_hash}/{context_id}/{task_id}/{artifact_id}-{name}
    -- Prefix layout is load-bearing: blob lifecycle policies match on prefix,
    -- and a deletion request must be a single prefix delete.
    blob_key     TEXT,
    sha256       TEXT,
    bytes        BIGINT,
    state        TEXT NOT NULL DEFAULT 'pending'
                 CHECK (state IN ('pending','stored','expired','failed')),
    upstream_ref JSONB,               -- {container_id, file_id} — transient
    harvested_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Harvest is idempotent: a duplicate callback re-resolves to the same row.
CREATE UNIQUE INDEX gw_artifact_dedupe ON gw_artifact (task_id, artifact_id);

-- Anything still pending is a harvest that lost its race with the container
-- TTL. Alert on it; the bytes are unrecoverable. Wire the runbook to this index.
CREATE INDEX gw_artifact_unharvested
    ON gw_artifact (created_at) WHERE state = 'pending';

-- Turn-by-turn A2A Message history. a2a-sdk's TaskManager already assembles
-- Task.history/status.message correctly in memory before every
-- TaskStore.save() call; this table is the persistence
-- GatewayTaskStoreAdapter never had for it. No context_id column: mirrors
-- gw_artifact's list_for_task, which enforces D1 by construction (only ever
-- queried for an already-authorised task_id), not by a join here.
CREATE TABLE gw_message (
    message_id  TEXT PRIMARY KEY,   -- a2a-sdk guarantees global uniqueness
    task_id     TEXT NOT NULL REFERENCES gw_task(task_id),
    seq         BIGSERIAL,          -- read order
    role        TEXT NOT NULL CHECK (role IN ('ROLE_UNSPECIFIED','ROLE_USER','ROLE_AGENT')),
    payload     JSONB NOT NULL,     -- full Message proto, google.protobuf.json_format
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX gw_message_by_task ON gw_message (task_id, seq);

-- User interjections into a running task (D7). Deliberately NOT in gw_event:
-- events are things the upstream told us, interjections are things the user
-- asked us to tell the upstream. Different direction, different lifecycle.
CREATE TABLE gw_interjection (
    task_id           TEXT NOT NULL REFERENCES gw_task(task_id),
    sequence          INT  NOT NULL,
    principal_subject TEXT NOT NULL,   -- must match gw_context; checked on write
    text              TEXT NOT NULL,   -- capped, control chars stripped
    state             TEXT NOT NULL DEFAULT 'pending'
                      CHECK (state IN ('pending','delivered','expired')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at      TIMESTAMPTZ,
    PRIMARY KEY (task_id, sequence)
);

-- Rate limiting and audit both read this.
CREATE INDEX gw_interjection_pending
    ON gw_interjection (task_id) WHERE state = 'pending';

CREATE TABLE gw_push_config (
    context_id  TEXT NOT NULL REFERENCES gw_context(context_id),
    url         TEXT NOT NULL,        -- SSRF-allowlisted at write time
    token_ref   TEXT,                 -- Key Vault reference, not the secret
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (context_id, url)
);

-- Dedupe on the A2A messageId rather than inventing an Idempotency-Key header.
CREATE TABLE gw_inbound_message (
    message_id  TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES gw_task(task_id),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Cross-replica event fan-in

```sql
CREATE FUNCTION gw_notify_event() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('gw_event', NEW.task_id);
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER gw_event_notify AFTER INSERT ON gw_event
  FOR EACH ROW EXECUTE FUNCTION gw_notify_event();
```

SSE handlers `LISTEN gw_event`, then read `gw_event WHERE task_id = $1 AND
sequence > $2`. Resumable, ordered, and correct across replicas — so "v1 is
a single server" does not become "v1 is a single uvicorn worker".

## T3 store topology

```
Postgres flexible server          <- shared: one backup policy, one network path
├── schema gateway.*              <- gw_task, gw_context, gw_event, gw_artifact
└── schema agentsrv.*             <- a2a-sdk DatabaseTaskStore
```

Same server, **separate schemas**, never the same table namespaced by app
name. Sharing a table doesn't reduce the double-store problem, it hides
it: two writers with different lifecycles on one row set, no clear owner
of a state transition, coupled migrations. `gateway.gw_task` is the system
of record; the agent server's store is a short-retention projection,
because the real durability already lives in Durable Task Scheduler.

## Azure Postgres with Entra auth

Tokens expire hourly. The async engine needs a token-refreshing creator, or
connections fail an hour after start-up. Easy to miss, painful to debug.
