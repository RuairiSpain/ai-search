"""The message passed between workflow executors (checkpointed at every step)."""

from __future__ import annotations

from pydantic import BaseModel


class PipelineState(BaseModel):
    operation_id: str
    tenant_id: str
    user_object_id: str
    prompt: str

    english_text: str = ""
    spanish_text: str = ""
    markdown_text: str = ""
    workspace_path: str = ""

    artifact_id: str = ""
    blob_name: str = ""
    display_name: str = ""
    size_bytes: int = 0
    created_at_iso: str = ""

    download_url: str = ""
    expires_at_iso: str = ""
