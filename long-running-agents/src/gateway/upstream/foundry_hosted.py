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
from gateway.upstream.base import Capabilities, ProgressFidelity, Submission, UpstreamRef
from gateway.upstream.foundry_responses import FoundryResponsesAdapter, _map_state, new_task_id

log = logging.getLogger(__name__)


class Forbidden(Exception):
    """Raised by the injected _project/_ping client on a 403. Adapters
    never import the concrete Azure exception type here — see docs/00
    design premise #3."""


class FoundryHostedAdapter(FoundryResponsesAdapter):
    capabilities = Capabilities(
        progress=ProgressFidelity.COARSE,  # promoted to FINE by the gw.progress.v1
        push=False,  # filter in follow() — docs/05 §5.4; no separate transport
        artifacts=True,
        input_required=False,
        cancel=True,
    )

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
    ):
        # Note: does NOT call FoundryResponsesAdapter.__init__ — the T2
        # OpenAI-shaped client is obtained per-call from the project
        # client (get_openai_client), not held once at construction.
        self._project = project_client
        self._agent_name = agent_name
        self._identity_mode = identity_mode
        self._interval = poll_interval_s
        self._openai = None  # unused; submit() below overrides the base class's entirely

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
        client = self._project.get_openai_client(agent_name=self._agent_name)
        await client.models.list(extra_headers=headers or dict(self._PREVIEW))
        return True

    async def submit(
        self, *, app: str, principal: Principal, ref: UpstreamRef, text: str, blocking: bool, budget_ms: int
    ) -> Submission:
        client = self._project.get_openai_client(agent_name=self._agent_name)
        resp = await client.responses.create(
            background=not blocking,
            conversation=ref.conversation_id,
            input=text,
            extra_headers=self._headers(principal),
            prompt_cache_key=principal.subject,
            safety_identifier=principal.subject,
        )
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
