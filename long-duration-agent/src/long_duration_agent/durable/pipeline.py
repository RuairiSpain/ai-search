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

Note: this module deliberately does *not* use
``from __future__ import annotations``. agent_framework's ``@response_handler``
signature validator inspects raw (unresolved) annotations rather than calling
``typing.get_type_hints``, so postponed evaluation breaks its
``WorkflowContext[...]`` detection on ``SteeringGateExecutor.on_steering_decision``.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, WorkflowEvent, handler, response_handler
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
from .state import PipelineState, SteeringConfirmation, SteeringDecision

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


class SteeringGateExecutor(Executor):
    """The single checkpoint before the artifact is copied to Blob Storage.

    Checks whether the user sent any steering messages while the agent was
    working. If nothing is queued, the pipeline proceeds straight to Upload -
    the common case is completely unaffected by this feature. If one or more
    messages arrived, they're concatenated with the current prompt and a HITL
    request asks the user whether to translate the combined text, edit it, or
    stop the operation entirely; ``durable/engine.py`` resumes this pause via
    ``POST /invocations/{operation_id}/respond``.
    """

    @handler
    async def process(self, state: PipelineState, ctx: WorkflowContext[PipelineState]) -> None:
        pending = get_metadata_store().drain_steering_messages(state.operation_id)
        if not pending:
            await ctx.send_message(state, target_id="upload")
            return

        concatenated = "\n\n".join([state.prompt, *pending])
        ctx.set_state("steering_base_state", state.model_dump())
        await ctx.add_event(
            _status_event(
                self.id,
                OrchestrationStage.STEERING_DETECTED,
                f"Received {len(pending)} new message(s) while working - checking with you before translating them.",
            )
        )
        await ctx.request_info(
            SteeringConfirmation(
                question=(
                    "You sent additional message(s) while I was working. "
                    "Translate the combined text below?"
                ),
                full_text=concatenated,
            ),
            SteeringDecision,
        )

    @response_handler
    async def on_steering_decision(
        self,
        original_request: SteeringConfirmation,
        response: SteeringDecision,
        ctx: WorkflowContext[PipelineState],
    ) -> None:
        base = PipelineState.model_validate(ctx.get_state("steering_base_state"))
        if response.action == "stop":
            await ctx.send_message(base, target_id="stop")
            return

        new_prompt = response.edited_text.strip() if response.action == "edit" else original_request.full_text
        if not new_prompt:
            # Nothing usable to edit to - fall back to the concatenated text rather than
            # silently dropping the loop.
            new_prompt = original_request.full_text
        # Restart from Validate (not straight back to Translate) so an edited or
        # concatenated prompt is re-checked against the character limit too.
        await ctx.send_message(base.model_copy(update={"prompt": new_prompt}), target_id="validate")


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


class StopExecutor(Executor):
    """Cancels the operation: cleans up the hosted agent's local file and any
    already-uploaded artifact. Reachable only via a "stop" HITL decision, which
    by construction happens before Upload ever runs - so blob_name is normally
    empty here; the delete is still attempted defensively."""

    @handler
    async def process(self, state: PipelineState, ctx: WorkflowContext[Never, PipelineState]) -> None:
        delete_workspace_file(state.operation_id)
        if state.blob_name:
            try:
                await get_blob_store().delete(state.blob_name)
            except FileNotFoundError:
                pass
        await ctx.add_event(
            WorkflowEvent(
                "data",
                executor_id=self.id,
                data={
                    "kind": "stopped",
                    "stage": OrchestrationStage.STOPPED.value,
                    "message": "The request was stopped at your request.",
                },
            )
        )
        await ctx.yield_output(state)


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


ALLOWED_CHECKPOINT_TYPES = [
    "long_duration_agent.durable.state:PipelineState",
    "long_duration_agent.durable.state:SteeringConfirmation",
    "long_duration_agent.durable.state:SteeringDecision",
]


def build_workflow(*, workflow_name: str, checkpoint_storage):
    validate = ValidateExecutor(id="validate")
    translate = TranslateExecutor(id="translate")
    save_markdown = SaveMarkdownExecutor(id="save_markdown")
    artifact_created = ArtifactCreatedExecutor(id="artifact_created")
    steering_gate = SteeringGateExecutor(id="steering_gate")
    upload = UploadExecutor(id="upload")
    cleanup_local = CleanupLocalExecutor(id="cleanup_local")
    link = LinkExecutor(id="link")
    stop = StopExecutor(id="stop")

    return (
        WorkflowBuilder(start_executor=validate, name=workflow_name, checkpoint_storage=checkpoint_storage)
        .add_edge(validate, translate)
        .add_edge(translate, save_markdown)
        .add_edge(save_markdown, artifact_created)
        .add_edge(artifact_created, steering_gate)
        .add_edge(steering_gate, upload)  # fast path: nothing queued
        .add_edge(steering_gate, validate)  # loop back: "yes" or "edit"
        .add_edge(steering_gate, stop)  # "stop"
        .add_edge(upload, cleanup_local)
        .add_edge(cleanup_local, link)
        .build()
    )
