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
    UPLOADED = "uploaded"
    LOCAL_CLEANED_UP = "local_cleaned_up"
    LINK_READY = "link_ready"
    COMPLETED = "completed"
    FAILED = "failed"


class StreamEvent(BaseModel):
    """One SSE event. ``event`` maps to the SSE ``event:`` field."""

    event: Literal["status", "artifact", "error", "completed"]
    stage: OrchestrationStage
    data: dict[str, Any]
    sequence: int
    emitted_at: datetime = Field(default_factory=utcnow)


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
