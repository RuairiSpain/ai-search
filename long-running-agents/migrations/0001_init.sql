-- Gateway schema. Beyond what a2a-sdk's DatabaseTaskStore creates.
-- Prefix gw_ to avoid collision with SDK-managed tables.
-- Source of truth: docs/03-postgres-schema.md — keep this file in sync.
--
-- Tables below live in the connecting user's default schema (`public` in
-- local dev). T3 gets a genuinely separate `agentsrv` schema on the same
-- server so a2a-sdk's DatabaseTaskStore never shares a table namespaced by
-- app name with these — see docs/03-postgres-schema.md "T3 store topology".

-- Per-user, per-app conversation continuity.
CREATE TABLE IF NOT EXISTS gw_context (
    context_id        TEXT PRIMARY KEY,
    app               TEXT NOT NULL,
    principal_subject TEXT NOT NULL,
    session_id        TEXT,          -- T2 agent_session_id
    conversation_id   TEXT,          -- T1/T2 Foundry conversation
    instance_url      TEXT,          -- T3 affinity pin (BYO-compute only; unused with DTS)
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- THE tier-2 safety net. The platform will not fence delegated users from
-- each other; this constraint is what actually prevents cross-user sandbox
-- reuse. Do not drop it for a "shared session" feature without a design review.
CREATE UNIQUE INDEX IF NOT EXISTS gw_context_session_owner
    ON gw_context (app, session_id)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS gw_context_by_principal
    ON gw_context (app, principal_subject, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS gw_task (
    task_id           TEXT PRIMARY KEY,
    context_id        TEXT NOT NULL REFERENCES gw_context(context_id),
    app               TEXT NOT NULL,
    tier              TEXT NOT NULL CHECK (tier IN ('t1','t2','t3')),
    state             TEXT NOT NULL,
    run_id            TEXT,
    last_sequence     INT  NOT NULL DEFAULT 0,
    -- wedged-task detection: a reaper fails tasks whose lease has lapsed
    lease_expires_at  TIMESTAMPTZ,
    heartbeat_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Reaper must exclude 'input-required' — a multi-day HITL approval is not
-- wedged. Lease duration is per-tier; see docs/08-open-items-and-experiments.md.
CREATE INDEX IF NOT EXISTS gw_task_reaper
    ON gw_task (lease_expires_at)
    WHERE state IN ('submitted','working');

-- Append-only. Cross-replica fan-in: a callback landing on worker 1 reaches
-- an SSE stream held by worker 2 via LISTEN/NOTIFY on insert.
CREATE TABLE IF NOT EXISTS gw_event (
    task_id     TEXT NOT NULL REFERENCES gw_task(task_id),
    sequence    INT  NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('status','artifact')),
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, sequence)   -- idempotent: duplicate callbacks no-op
);

-- Artifacts live in blob storage; this table is the index and the authz record.
-- The upstream reference is TRANSIENT (a code interpreter container dies in
-- ~1 hour) and is never the canonical location. See
-- docs/07-artifacts-and-code-interpreter.md.
CREATE TABLE IF NOT EXISTS gw_artifact (
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
CREATE UNIQUE INDEX IF NOT EXISTS gw_artifact_dedupe ON gw_artifact (task_id, artifact_id);

-- Anything still pending is a harvest that lost its race with the container
-- TTL. Alert on it; the bytes are unrecoverable. Wire the runbook to this index.
CREATE INDEX IF NOT EXISTS gw_artifact_unharvested
    ON gw_artifact (created_at) WHERE state = 'pending';

-- User interjections into a running task (D7). Deliberately NOT in gw_event:
-- events are things the upstream told us, interjections are things the user
-- asked us to tell the upstream. Different direction, different lifecycle.
CREATE TABLE IF NOT EXISTS gw_interjection (
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
CREATE INDEX IF NOT EXISTS gw_interjection_pending
    ON gw_interjection (task_id) WHERE state = 'pending';

CREATE TABLE IF NOT EXISTS gw_push_config (
    context_id  TEXT NOT NULL REFERENCES gw_context(context_id),
    url         TEXT NOT NULL,        -- SSRF-allowlisted at write time
    token_ref   TEXT,                 -- Key Vault reference, not the secret
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (context_id, url)
);

-- Dedupe on the A2A messageId rather than inventing an Idempotency-Key header.
CREATE TABLE IF NOT EXISTS gw_inbound_message (
    message_id  TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES gw_task(task_id),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cross-replica event fan-in.
CREATE OR REPLACE FUNCTION gw_notify_event() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('gw_event', NEW.task_id);
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS gw_event_notify ON gw_event;
CREATE TRIGGER gw_event_notify AFTER INSERT ON gw_event
  FOR EACH ROW EXECUTE FUNCTION gw_notify_event();

-- T3 gets its own schema on the same server — never the same table
-- namespaced by app name (docs/03-postgres-schema.md "T3 store topology").
-- a2a-sdk's DatabaseTaskStore creates its own tables inside it; this
-- migration only ensures the schema exists as a placement target.
CREATE SCHEMA IF NOT EXISTS agentsrv;
