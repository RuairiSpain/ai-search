"""Runs the translation pipeline as a durable, resumable, idempotent operation.

- Idempotent: replaying the same ``operation_id`` never redoes finished work.
  A completed operation just gets a freshly minted download link; an
  in-progress one (e.g. the process crashed mid-pipeline) resumes from its
  last checkpoint instead of starting over.
- Durable: checkpoints are written to disk (``FileCheckpointStorage``) after
  every step. Swap this for a distributed ``CheckpointStorage`` backend
  (Cosmos DB, Table Storage, or Azure Functions' own Durable Task store via
  ``agent_framework_durabletask``) to scale beyond a single host - the
  ``Workflow`` object in ``pipeline.py`` does not change.
- Streamed: this is an async generator of ``StreamEvent`` so the hosted-agent
  Invocations endpoint can forward each one over SSE as soon as it happens.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from agent_framework import FileCheckpointStorage

from ..config import get_settings
from ..identity import CallerIdentity
from ..models import InvocationRequest, OrchestrationStage, StreamEvent
from ..storage.metadata_store import get_metadata_store
from .pipeline import ALLOWED_CHECKPOINT_TYPES, build_workflow
from .state import PipelineState

logger = logging.getLogger(__name__)

_checkpoint_storage: FileCheckpointStorage | None = None


def _get_checkpoint_storage() -> FileCheckpointStorage:
    global _checkpoint_storage
    if _checkpoint_storage is None:
        settings = get_settings()
        _checkpoint_storage = FileCheckpointStorage(
            storage_path=str(settings.state_db_path.parent / "checkpoints"),
            allowed_checkpoint_types=ALLOWED_CHECKPOINT_TYPES,
        )
    return _checkpoint_storage


def reset_checkpoint_storage_cache() -> None:
    """Test helper: forces the next call to rebuild storage from current settings."""
    global _checkpoint_storage
    _checkpoint_storage = None


def _workflow_name_for(operation_id: str) -> str:
    return f"lda-translate-{operation_id}"


class OperationFailedError(RuntimeError):
    pass


async def run_translation_operation(
    request: InvocationRequest, caller: CallerIdentity
) -> AsyncIterator[StreamEvent]:
    operation_id = request.operation_id or _new_operation_id()
    workflow_name = _workflow_name_for(operation_id)
    store = get_metadata_store()
    checkpoint_storage = _get_checkpoint_storage()

    sequence = 0

    def next_event(event: str, stage: OrchestrationStage, data: dict) -> StreamEvent:
        nonlocal sequence
        sequence += 1
        return StreamEvent(event=event, stage=stage, data=data, sequence=sequence)

    existing = store.get_operation(operation_id)

    if existing is not None and existing["status"] == "completed":
        # Idempotent replay of a finished operation: no re-translation, no re-upload -
        # just a brand-new 15-minute download link for the same artifact.
        artifact = store.get_artifact(existing["artifact_id"])
        if artifact is not None:
            from ..broker.tokens import build_download_link

            download_url, expires_at = build_download_link(
                artifact_id=artifact.artifact_id,
                tenant_id=artifact.tenant_id,
                user_object_id=artifact.user_object_id,
            )
            yield next_event(
                "status",
                OrchestrationStage.COMPLETED,
                {"message": "This request was already completed. Here is a fresh download link."},
            )
            yield next_event(
                "artifact",
                OrchestrationStage.LINK_READY,
                {
                    "artifact_id": artifact.artifact_id,
                    "display_name": artifact.display_name,
                    "download_url": download_url,
                    "expires_at": expires_at.isoformat(),
                },
            )
            yield next_event("completed", OrchestrationStage.COMPLETED, {"success": True})
            return

    store.start_operation(
        operation_id=operation_id,
        workflow_name=workflow_name,
        tenant_id=caller.tenant_id,
        user_object_id=caller.user_object_id,
    )

    workflow = build_workflow(workflow_name=workflow_name, checkpoint_storage=checkpoint_storage)

    resume_checkpoint_id = None
    if existing is not None and existing["status"] == "in_progress":
        latest = await checkpoint_storage.get_latest(workflow_name=workflow_name)
        if latest is not None:
            resume_checkpoint_id = latest.checkpoint_id

    try:
        if resume_checkpoint_id:
            stream = workflow.run(
                checkpoint_id=resume_checkpoint_id, checkpoint_storage=checkpoint_storage, stream=True
            )
        else:
            initial_state = PipelineState(
                operation_id=operation_id,
                tenant_id=caller.tenant_id,
                user_object_id=caller.user_object_id,
                prompt=request.prompt,
            )
            stream = workflow.run(initial_state, stream=True, checkpoint_storage=checkpoint_storage)

        async for event in stream:
            mapped = _map_workflow_event(event, next_event)
            if mapped is not None:
                yield mapped

        result = await stream.get_final_response()
        outputs = result.get_outputs()
        if not outputs:
            raise OperationFailedError("Workflow completed without producing an artifact link.")
        final_state: PipelineState = outputs[0]
        store.complete_operation(operation_id, artifact_id=final_state.artifact_id)
        yield next_event("completed", OrchestrationStage.COMPLETED, {"success": True})

    except Exception as exc:  # noqa: BLE001 - reported to the caller, then re-raised for logs
        logger.exception("Operation %s failed", operation_id)
        store.fail_operation(operation_id, error=str(exc))
        yield next_event("error", OrchestrationStage.FAILED, {"message": str(exc)})


def _map_workflow_event(event, next_event) -> StreamEvent | None:
    if getattr(event, "type", None) != "data" or not isinstance(event.data, dict):
        return None
    data = dict(event.data)
    kind = data.pop("kind", "status")
    stage_value = data.pop("stage", OrchestrationStage.STARTED.value)
    stage = OrchestrationStage(stage_value)
    return next_event(kind, stage, data)


def _new_operation_id() -> str:
    import uuid

    return str(uuid.uuid4())
