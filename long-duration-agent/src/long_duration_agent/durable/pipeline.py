"""The translation-artifact pipeline, expressed as a Microsoft Agent Framework Workflow.

Each user-visible step from the spec is one Executor. The Workflow engine
checkpoints state after every executor completes (when given
``checkpoint_storage``), so a crash between any two steps resumes from the
last completed step rather than re-running the whole pipeline - this is the
"durable task" property the design calls for, without a hand-rolled
step-runner.

Status updates are emitted as custom ``WorkflowEvent`` "data" events via
``ctx.add_event(...)``; ``durable/engine.py`` turns those into the SSE stream
the hosted-agent Invocations endpoint sends to the chat UI. This same
``Workflow`` object is what you would hand to
``agent_framework_durabletask``'s Azure Functions Durable Task host for a
production deployment - see docs/architecture.md.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, WorkflowEvent, handler
from typing_extensions import Never

from ..broker.tokens import build_download_link
from ..config import get_settings
from ..limits import validate_markdown_size, validate_prompt_length
from ..markdown_artifact import build_markdown
from ..models import ArtifactRecord, OrchestrationStage
from ..storage.blob_store import get_blob_store
from ..storage.metadata_store import get_metadata_store
from ..translator import translate_to_spanish
from ..workspace import delete_workspace_file, write_workspace_file
from .state import PipelineState

logger = logging.getLogger(__name__)


def _status_event(executor_id: str, stage: OrchestrationStage, message: str) -> WorkflowEvent:
    return WorkflowEvent(
        "data",
        executor_id=executor_id,
        data={"kind": "status", "stage": stage.value, "message": message},
    )


class ValidateExecutor(Executor):
    """Validates the prompt and announces the agent has started."""

    @handler
    async def process(self, state: PipelineState, ctx: WorkflowContext[PipelineState]) -> None:
        validate_prompt_length(state.prompt)
        await ctx.add_event(_status_event(self.id, OrchestrationStage.STARTED, "The agent is working..."))
        await ctx.send_message(state.model_copy(update={"english_text": state.prompt}))


class TranslateExecutor(Executor):
    """Translates the prompt into Spain Spanish and announces completion."""

    @handler
    async def process(self, state: PipelineState, ctx: WorkflowContext[PipelineState]) -> None:
        spanish_text = await translate_to_spanish(state.prompt)
        await ctx.add_event(
            _status_event(self.id, OrchestrationStage.TRANSLATED, "The text has been translated.")
        )
        await ctx.send_message(state.model_copy(update={"spanish_text": spanish_text}))


class SaveMarkdownExecutor(Executor):
    """Writes the bilingual Markdown artifact to the hosted agent's local scratch workspace."""

    @handler
    async def process(self, state: PipelineState, ctx: WorkflowContext[PipelineState]) -> None:
        created_at = datetime.now(timezone.utc)
        # Deterministic id from the operation id, so a retried/resumed operation
        # never produces two different artifacts for the same request.
        artifact_id = state.operation_id
        markdown = build_markdown(
            artifact_id=artifact_id,
            created_at=created_at,
            english_text=state.english_text,
            spanish_text=state.spanish_text,
        )
        validate_markdown_size(markdown)
        workspace_path = write_workspace_file(state.operation_id, markdown)
        await ctx.send_message(
            state.model_copy(
                update={
                    "markdown_text": markdown,
                    "artifact_id": artifact_id,
                    "display_name": f"translation-{artifact_id}-es-ES.md",
                    "workspace_path": str(workspace_path),
                    "created_at_iso": created_at.isoformat(),
                }
            )
        )


class ArtifactCreatedExecutor(Executor):
    """Waits 5 seconds, then announces the artifact was created successfully."""

    @handler
    async def process(self, state: PipelineState, ctx: WorkflowContext[PipelineState]) -> None:
        await asyncio.sleep(get_settings().lda_wait_after_save_seconds)
        await ctx.add_event(
            _status_event(
                self.id, OrchestrationStage.ARTIFACT_CREATED, "The artifact was created successfully."
            )
        )
        await ctx.send_message(state)


class UploadExecutor(Executor):
    """Waits 2 seconds, then uploads the artifact to durable, private Blob Storage."""

    @handler
    async def process(self, state: PipelineState, ctx: WorkflowContext[PipelineState]) -> None:
        settings = get_settings()
        await asyncio.sleep(settings.lda_wait_before_upload_seconds)
        blob_name = f"users/{state.tenant_id}/{state.user_object_id}/{state.artifact_id}.md"
        size_bytes = await get_blob_store().upload_file(
            local_path=Path(state.workspace_path), blob_name=blob_name
        )

        created_at = datetime.fromisoformat(state.created_at_iso)
        expires_at = created_at + timedelta(hours=settings.lda_artifact_ttl_hours)
        get_metadata_store().save_artifact(
            ArtifactRecord(
                artifact_id=state.artifact_id,
                operation_id=state.operation_id,
                tenant_id=state.tenant_id,
                user_object_id=state.user_object_id,
                blob_container=settings.azure_storage_container,
                blob_name=blob_name,
                display_name=state.display_name,
                size_bytes=size_bytes,
                created_at=created_at,
                expires_at=expires_at,
            )
        )
        await ctx.add_event(
            _status_event(self.id, OrchestrationStage.UPLOADED, "The artifact was saved to secure storage.")
        )
        await ctx.send_message(state.model_copy(update={"blob_name": blob_name, "size_bytes": size_bytes}))


class CleanupLocalExecutor(Executor):
    """Deletes the local hosted-agent scratch copy now that the durable copy exists."""

    @handler
    async def process(self, state: PipelineState, ctx: WorkflowContext[PipelineState]) -> None:
        delete_workspace_file(state.operation_id)
        await ctx.send_message(state)


class LinkExecutor(Executor):
    """Mints a fresh, short-lived broker download link and yields the final result."""

    @handler
    async def process(self, state: PipelineState, ctx: WorkflowContext[Never, PipelineState]) -> None:
        download_url, expires_at = build_download_link(
            artifact_id=state.artifact_id, tenant_id=state.tenant_id, user_object_id=state.user_object_id
        )
        final_state = state.model_copy(
            update={"download_url": download_url, "expires_at_iso": expires_at.isoformat()}
        )
        await ctx.add_event(
            WorkflowEvent(
                "data",
                executor_id=self.id,
                data={
                    "kind": "artifact",
                    "stage": OrchestrationStage.LINK_READY.value,
                    "artifact_id": final_state.artifact_id,
                    "display_name": final_state.display_name,
                    "download_url": download_url,
                    "expires_at": expires_at.isoformat(),
                },
            )
        )
        await ctx.yield_output(final_state)


ALLOWED_CHECKPOINT_TYPES = ["long_duration_agent.durable.state:PipelineState"]


def build_workflow(*, workflow_name: str, checkpoint_storage):
    validate = ValidateExecutor(id="validate")
    translate = TranslateExecutor(id="translate")
    save_markdown = SaveMarkdownExecutor(id="save_markdown")
    artifact_created = ArtifactCreatedExecutor(id="artifact_created")
    upload = UploadExecutor(id="upload")
    cleanup_local = CleanupLocalExecutor(id="cleanup_local")
    link = LinkExecutor(id="link")

    return (
        WorkflowBuilder(start_executor=validate, name=workflow_name, checkpoint_storage=checkpoint_storage)
        .add_edge(validate, translate)
        .add_edge(translate, save_markdown)
        .add_edge(save_markdown, artifact_created)
        .add_edge(artifact_created, upload)
        .add_edge(upload, cleanup_local)
        .add_edge(cleanup_local, link)
        .build()
    )
