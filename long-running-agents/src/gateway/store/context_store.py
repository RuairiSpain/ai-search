"""gw_context: per-user, per-app conversation continuity, and THE
authorisation boundary and outermost layer for every gateway tier
(docs/02-decisions.md D1).

Two rules enforced here, both load-bearing:

1. `authorise_context` returns a row only when principal_subject matches
   in the SQL WHERE clause — never "fetch then check in Python". There is
   no code path that reads a context row without the principal as part of
   the query.
2. `record_upstream_ref` is guarded by a Postgres advisory lock keyed on
   (app, principal_subject, context_id) to fix the session-creation race
   documented in docs/05-tier2-hosted-agents.md §6.3: two concurrent first
   turns (two tabs, or a retry) must not each mint a new T2 session for
   the same context_id. The loser gets told to discard/terminate the
   upstream session it just created rather than leak it.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import zlib
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from gateway.auth.principal import Principal
from gateway.upstream.base import UpstreamRef


@dataclass(frozen=True)
class ContextRow:
    context_id: str
    app: str
    principal_subject: str
    session_id: str | None
    conversation_id: str | None
    instance_url: str | None
    created_at: datetime
    last_seen_at: datetime

    def upstream_ref(self) -> UpstreamRef:
        return UpstreamRef(
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            instance_url=self.instance_url,
        )


def _advisory_lock_key(app: str, principal_subject: str, context_id: str) -> int:
    # pg_advisory_xact_lock takes a bigint; crc32 keeps this deterministic
    # and small without pulling in a hashing extension.
    raw = f"{app}\0{principal_subject}\0{context_id}".encode()
    return zlib.crc32(raw)


def _row_to_context(row: asyncpg.Record) -> ContextRow:
    return ContextRow(
        context_id=row["context_id"],
        app=row["app"],
        principal_subject=row["principal_subject"],
        session_id=row["session_id"],
        conversation_id=row["conversation_id"],
        instance_url=row["instance_url"],
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
    )


class ContextStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def new_context(self, app: str, principal: Principal) -> ContextRow:
        """A2A `contextId` is opaque and gateway-issued — the client never
        supplies one for a brand-new conversation (docs/02-decisions.md
        D1 "Where the conversation ID comes from"). Deliberately random,
        never derived from the user id (D1 "Rejected: deriving IDs")."""
        context_id = f"ctx_{secrets.token_urlsafe(24)}"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO gw_context (context_id, app, principal_subject)
                VALUES ($1, $2, $3)
                ON CONFLICT (context_id) DO NOTHING
                RETURNING *
                """,
                context_id,
                app,
                principal.subject,
            )
            if row is None:
                # Astronomically unlikely token collision; regenerate once.
                return await self.new_context(app, principal)
            return _row_to_context(row)

    async def get_or_create_context(
        self, context_id: str, app: str, principal: Principal
    ) -> ContextRow:
        """Used by the a2a-sdk integration, where `context_id` is already
        resolved by the SDK before our code runs — either client-supplied
        (A2A lets a client propose a contextId to group related tasks) or
        freshly minted by the SDK's own UUID generator when the client
        omitted one. Either way we no longer choose the id ourselves, which
        is a deliberate, spec-conformant loosening of D1's letter ("the
        gateway creates it") while preserving its actual security property:
        ownership is still principal-scoped and atomic, so a client can
        never claim a context_id that already belongs to someone else.

        Atomic INSERT-or-authorise, not check-then-act: attempt to create
        the row with this exact id first. If that id is free, we now own
        it — success. If it's taken (by us or by someone else), only THEN
        check ownership via authorise_context's principal-scoped query.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO gw_context (context_id, app, principal_subject)
                VALUES ($1, $2, $3)
                ON CONFLICT (context_id) DO NOTHING
                RETURNING *
                """,
                context_id,
                app,
                principal.subject,
            )
            if row is not None:
                return _row_to_context(row)

        owned = await self.authorise_context(context_id, principal)
        if owned is None:
            raise PermissionError(f"context {context_id!r} is not owned by this principal")
        return owned

    async def authorise_context(self, context_id: str, principal: Principal) -> ContextRow | None:
        """THE control (D1). A client-supplied contextId that is not
        authorised against this principal is a direct IDOR. The API layer
        must return 404 (not 403) on None — don't confirm the id exists.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE gw_context
                SET last_seen_at = now()
                WHERE context_id = $1 AND principal_subject = $2
                RETURNING *
                """,
                context_id,
                principal.subject,
            )
            return _row_to_context(row) if row else None

    async def record_upstream_ref(
        self, context_id: str, principal: Principal, ref: UpstreamRef
    ) -> tuple[ContextRow, bool]:
        """Populate session_id/conversation_id/instance_url for a context
        that doesn't have them yet. Returns (row, won) — `won=False` means
        another concurrent request already populated the ref first; the
        caller must treat the upstream session/conversation *it* just
        created as an orphan and terminate it rather than leak it.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            ctx = await conn.fetchrow(
                "SELECT * FROM gw_context WHERE context_id = $1 AND principal_subject = $2",
                context_id,
                principal.subject,
            )
            if ctx is None:
                raise LookupError(f"no context {context_id!r} for this principal")

            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)",
                _advisory_lock_key(ctx["app"], principal.subject, context_id),
            )
            # Re-read inside the lock: another request may have
            # populated the ref while we waited for it.
            ctx = await conn.fetchrow(
                "SELECT * FROM gw_context WHERE context_id = $1", context_id
            )
            already_populated = bool(ctx["session_id"] or ctx["conversation_id"])
            if already_populated:
                return _row_to_context(ctx), False

            row = await conn.fetchrow(
                """
                    UPDATE gw_context
                    SET session_id = $2, conversation_id = $3, instance_url = $4,
                        last_seen_at = now()
                    WHERE context_id = $1
                    RETURNING *
                    """,
                context_id,
                ref.session_id,
                ref.conversation_id,
                ref.instance_url,
            )
            return _row_to_context(row), True


def principal_metadata_stamp(principal: Principal, app: str, pepper: bytes) -> dict[str, str]:
    """Layer 3 of D1: a salted hash of the principal, stamped onto the
    Foundry conversation's metadata at creation and re-checked on every
    resume. Catches gateway mapping bugs the unique index wouldn't."""
    digest = hmac.new(pepper, principal.subject.encode(), hashlib.sha256).hexdigest()[:32]
    return {"gw_principal": digest, "gw_app": app}


def verify_principal_stamp(
    metadata: dict[str, str], principal: Principal, app: str, pepper: bytes
) -> bool:
    expected = principal_metadata_stamp(principal, app, pepper)
    return (
        hmac.compare_digest(metadata.get("gw_principal", ""), expected["gw_principal"])
        and metadata.get("gw_app") == expected["gw_app"]
    )
