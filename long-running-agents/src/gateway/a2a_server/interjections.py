"""Mid-run steering (D7), exposed as a gateway-owned REST extension, not
an A2A method. A2A itself does not define client-initiated messages
against a task that's still `working` (verified against the spec while
building `a2a_server/executor.py` — `_continue_existing` rejects exactly
that case with `UnsupportedOperationError`), so steering can't live on the
standard `message/send` surface. This is the side channel D7 always
assumed would exist; it just hadn't been built yet.

`POST /apps/{app}/tasks/{task_id}/interject` — same IDOR posture as every
other task-scoped endpoint (own the task's context or get a 404, never a
403), gated on the upstream actually declaring steering support, and
recorded in `gw_interjection` for audit regardless of outcome.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from gateway.auth.principal import AuthError, EntraValidator
from gateway.store.context_store import ContextStore
from gateway.store.interjection_store import InterjectionStore
from gateway.store.task_store import TaskStore
from gateway.upstream.base import SteeringMode, UpstreamAdapter

_MAX_TEXT_LEN = 2000


class InterjectRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TEXT_LEN)


def _sanitize(text: str) -> str:
    """Strips control characters (keeping newline/tab) and caps length —
    D7's security envelope is "advisory framing, no urgency markers, never
    raw"; the actual advisory wrapping is adapter-specific (e.g.
    `FoundryResponsesAdapter.steer()`'s `<user_interjection>` tag), but
    control-character stripping and length capping belong here, once, at
    the one place every interjection passes through regardless of tier."""
    stripped = "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
    )
    collapsed = re.sub(r"\s+", " ", stripped).strip()
    return collapsed[:_MAX_TEXT_LEN]


def build_interjection_router(
    *,
    prefix: str,
    app_cfg_name: str,
    adapter: UpstreamAdapter,
    validator: EntraValidator,
    contexts: ContextStore,
    tasks: TaskStore,
    interjections: InterjectionStore,
) -> APIRouter:
    router = APIRouter()

    @router.post(f"{prefix}/tasks/{{task_id}}/interject")
    async def interject(task_id: str, body: InterjectRequest, request: Request) -> dict:
        try:
            principal = validator.principal_from(request.headers.get("authorization"))
        except AuthError as exc:
            raise HTTPException(401) from exc

        # 404, not 403 — same IDOR posture as tasks/get (D1).
        task_row = await tasks.get_task(task_id)
        if task_row is None:
            raise HTTPException(404)
        ctx_row = await contexts.authorise_context(task_row.context_id, principal)
        if ctx_row is None:
            raise HTTPException(404)

        if adapter.capabilities.steering == SteeringMode.NONE:
            raise HTTPException(409, f"{app_cfg_name} does not support mid-run steering")
        if task_row.state != "working":
            raise HTTPException(
                409, f"task {task_id} is {task_row.state!r}, not working — nothing to steer"
            )

        text = _sanitize(body.text)
        ref = replace(ctx_row.upstream_ref(), run_id=task_row.run_id)
        result = await adapter.steer(ref, principal=principal, text=text)

        row = await interjections.record(
            task_id=task_id,
            principal_subject=principal.subject,
            text=text,
            delivered=result.outcome != "unsupported",
        )
        return {
            "outcome": result.outcome,
            "appliesAt": result.applies_at,
            "sequence": row.sequence,
        }

    return router
