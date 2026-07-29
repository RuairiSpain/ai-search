"""Shared pydantic models for requests, streamed events, and artifact records."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CallerIdentity(BaseModel):
    """The authenticated user the request is running on behalf of.

    Derived server-side from the caller's Entra token (or OBO context when the
    invocation arrives via Teams/Copilot Studio). Never trust a tenant/user id
    supplied in the request body.
    """

    tenant_id: str
    user_object_id: str
    display_name: Optional[str] = None

    @property
    def owner_key(self) -> str:
        return f"{self.tenant_id}/{self.user_object_id}"


class InvocationRequest(BaseModel):
    """Body of ``POST /invocations``."""

    prompt: str = Field(..., description="English text to translate.")
    operation_id: Optional[str] = Field(
        default=None,
        description=(
            "Idempotency key / stable operation id. If the same operation_id is "
            "replayed (client retry, reconnect), the orchestration resumes from "
            "its last checkpoint instead of redoing work."
        ),
    )
    conversation_id: Optional[str] = None


class OrchestrationStage(str, Enum):
    STARTED = "started"
    VALIDATED = "validated"
    TRANSLATED = "translated"
    MARKDOWN_SAVED = "markdown_saved"
    ARTIFACT_CREATED = "artifact_created"
    STEERING_DETECTED = "steering_detected"
    HITL_PENDING = "hitl_pending"
    UPLOADED = "uploaded"
    LOCAL_CLEANED_UP = "local_cleaned_up"
    LINK_READY = "link_ready"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class StreamEvent(BaseModel):
    """One SSE event. ``event`` maps to the SSE ``event:`` field."""

    event: Literal["status", "artifact", "error", "completed", "hitl_request", "stopped"]
    stage: OrchestrationStage
    data: dict[str, Any]
    sequence: int
    emitted_at: datetime = Field(default_factory=utcnow)


class SteerRequest(BaseModel):
    """Body of ``POST /invocations/{operation_id}/steer``.

    A message the user sends while the agent is still working. It is queued,
    not applied immediately - the workflow only picks it up (and asks for
    HITL confirmation before acting on it) at its next steering checkpoint,
    which always runs before the artifact is copied to Blob Storage.
    """

    text: str = Field(..., description="Additional/steering text from the user.")


class HitlDecisionRequest(BaseModel):
    """Body of ``POST /invocations/{operation_id}/respond`` - the user's answer to a HITL request.

    - "yes": translate the concatenated text shown in the HITL request as-is.
    - "edit": translate ``edited_text`` instead (fully replaces the prompt; re-validated
      against the character limit like any other prompt).
    - "stop": cancel the operation. The hosted agent's local file and any already-uploaded
      artifact are deleted; no download link is produced.
    """

    decision: Literal["yes", "edit", "stop"]
    edited_text: str = ""


class ArtifactRecord(BaseModel):
    """Metadata persisted for a saved artifact. No SAS/credential is ever stored here."""

    artifact_id: str
    operation_id: str
    tenant_id: str
    user_object_id: str
    blob_container: str
    blob_name: str
    display_name: str
    content_type: str = "text/markdown; charset=utf-8"
    size_bytes: int
    created_at: datetime
    expires_at: datetime
    status: Literal["active", "expired", "deleted"] = "active"


class ArtifactLink(BaseModel):
    artifact_id: str
    download_url: str
    expires_at: datetime
