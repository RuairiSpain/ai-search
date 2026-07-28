"""T2 adapter — hosted agent. Identical to the shared FoundryResponsesAdapter
base apart from headers and the session id; that small delta is the whole
point of the shared interface.

docs/01-gateway-config-and-adapter-contract.md §2 "T2 — hosted agent",
docs/05-tier2-hosted-agents.md §3 (identity delegation reference impl).
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from gateway.auth.principal import Principal
from gateway.upstream.base import (
    InboundFile,
    Submission,
    UpstreamRef,
)
from gateway.upstream.foundry_responses import (
    FoundryResponsesAdapter,
    _build_input,
    _map_state,
    _to_text_format,
    _upload_files,
    new_task_id,
)

log = logging.getLogger(__name__)


class Forbidden(Exception):
    """Raised by the injected _project/_ping client on a 403. Adapters
    never import the concrete Azure exception type here — see docs/00
    design premise #3."""


class FoundryHostedAdapter(FoundryResponsesAdapter):
    # `capabilities` is inherited as-is from FoundryResponsesAdapter (a
    # @property there) -- this class used to redeclare an identical
    # Capabilities(...) class attribute here, byte-for-byte the same as
    # the base class's value. Deleted rather than kept in sync by hand:
    # now that `input_required` is derived from `self._output_schema`
    # (docs/02-decisions.md D4), duplicating it here would mean two
    # places to update instead of one, for no behavioral difference.

    # Platform fact, not configuration. Delete this constant when the
    # preview flag goes GA rather than editing every upstream entry.
    _PREVIEW: ClassVar[dict[str, str]] = {"Foundry-Features": "HostedAgents=V1Preview"}

    def __init__(
        self,
        *,
        project_client: Any,
        agent_name: str,
        identity_mode: str = "per_user",
        poll_interval_s: float = 1.5,
        project_endpoint: str | None = None,
        credential: Any | None = None,
        output_schema: dict | None = None,
    ):
        # Note: does NOT call FoundryResponsesAdapter.__init__ — `_openai`
        # is a property below (a fresh per-call client, matching T2's
        # actual client lifecycle), which a plain instance assignment from
        # the base __init__ would shadow incompatibly.
        self._project = project_client
        self._agent_name = agent_name
        self._identity_mode = identity_mode
        self._interval = poll_interval_s
        # Only needed for fetch_artifact_bytes() (inherited, unchanged) —
        # same purpose as in the base class, just plumbed through
        # separately since __init__ doesn't chain to it. Previously never
        # set at all on this class, so fetch_artifact_bytes() would have
        # raised AttributeError on first use, same bug class as _openai
        # below.
        self._project_endpoint = project_endpoint
        self._credential = credential
        # Same independent-assignment reasoning as above -- __init__
        # doesn't chain to the base class's, so `input_required` capability
        # detection (inherited `capabilities` property, D4) needs its own
        # copy of this state here too.
        self._output_schema = output_schema
        self._text_format = _to_text_format(output_schema) if output_schema else None

    @property
    def _openai(self) -> Any:
        """Fresh per access, not cached at construction — matches T2's
        actual client lifecycle (docs/05: sessions/tokens are per-call
        state, not held). Fixes a real bug: `follow()`/`resume()`/
        `steer()`/`cancel()` are all inherited from `FoundryResponsesAdapter`
        and reference `self._openai` directly. Before this property existed,
        `self._openai` was unconditionally `None` on every
        `FoundryHostedAdapter` instance (only `submit()` and `artifact_url()`
        are overridden here), so every one of those calls would have raised
        `AttributeError` the first time it actually ran against a real T2
        task — never caught by tests, which all stand a `FakeAdapter` in
        for the whole `UpstreamAdapter` Protocol rather than exercising
        this class."""
        return self._project.get_openai_client(agent_name=self._agent_name)

    def _headers(self, principal: Principal) -> dict[str, str]:
        h = dict(self._PREVIEW)
        if self._identity_mode == "per_user":
            h["x-ms-user-identity"] = principal.user_identity_header()
        return h

    async def health(self) -> bool:
        """Probe delegation at startup, not at first real user request.

        A missing UserIdentityImpersonation grant is an Azure RBAC fact
        that YAML cannot assert (see infra/scripts/grant-agent-access.sh).
        Assert it here and fail readiness instead.
        """
        if self._identity_mode != "per_user":
            return await self._ping()
        probe = Principal(subject="gateway-readiness-probe", tenant="probe")
        try:
            await self._ping(headers=self._headers(probe))
        except Forbidden:
            log.error(
                "upstream %s: gateway identity lacks UserIdentityImpersonation; "
                "per-user isolation is NOT in effect",
                self._agent_name,
            )
            return False
        return True

    async def _ping(self, *, headers: dict[str, str] | None = None) -> bool:
        await self._openai.models.list(extra_headers=headers or dict(self._PREVIEW))
        return True

    async def submit(
        self,
        *,
        app: str,
        principal: Principal,
        ref: UpstreamRef,
        text: str,
        files: list[InboundFile],
        blocking: bool,
        budget_ms: int,
    ) -> Submission:
        uploaded = await _upload_files(self._openai, files)
        kwargs: dict[str, Any] = dict(
            background=not blocking,
            conversation=ref.conversation_id,
            input=_build_input(text, uploaded),
            extra_headers=self._headers(principal),
            prompt_cache_key=principal.subject,
            safety_identifier=principal.subject,
        )
        if self._text_format is not None:
            kwargs["text"] = self._text_format
        resp = await self._openai.responses.create(**kwargs)
        session_id = resp.model_extra.get("agent_session_id") if hasattr(resp, "model_extra") else None
        return Submission(
            task_id=new_task_id(),
            context_id=ref.conversation_id or resp.conversation.id,
            state=_map_state(resp.status),
            ref=UpstreamRef(
                session_id=session_id,
                conversation_id=resp.conversation.id,
                run_id=resp.id,
            ),
        )

    async def artifact_url(self, ref: UpstreamRef, artifact_id: str, *, principal: Principal) -> str:
        # Session Files API — already identity-scoped upstream.
        return await self._project.session_files.download_url(
            session_id=ref.session_id,
            path=artifact_id,
            headers=self._headers(principal),
        )

    async def terminate_session(self, session_id: str) -> None:
        """Stops the orphaned session left behind when this request loses
        a session-creation race (docs/05 §6.3, `ContextStore.record_upstream_ref`).
        Optional adapter capability, duck-typed by the caller (`getattr(adapter,
        "terminate_session", None)` in the executor) the same way
        `fetch_artifact_bytes` is — real, documented `azure-ai-projects`
        operation (`AgentsOperations.stop_session`, confirmed present on
        `AIProjectClient.agents` in the installed package, not a guessed
        REST endpoint like some other unverified integration points in
        this codebase)."""
        await self._project.agents.stop_session(agent_name=self._agent_name, session_id=session_id)
