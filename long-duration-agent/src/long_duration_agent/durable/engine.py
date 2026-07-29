"""Runs the translation pipeline as a durable, resumable, idempotent operation.

- Idempotent: replaying the same ``operation_id`` never redoes finished work.
  A completed operation just gets a freshly minted download link; an
  in-progress one (e.g. the process crashed mid-pipeline) resumes from its
  last checkpoint instead of starting over.
- Durable: checkpoints persist after every step, via ``FileCheckpointStorage``
  (single host/demo) or ``TableCheckpointStorage`` (``LDA_CHECKPOINT_BACKEND=
  azurite|azure`` - multi-instance-safe Azure Table Storage) - the ``Workflow``
  object in ``pipeline.py`` doesn't change either way.
- Streamed: this is an async generator of ``StreamEvent`` so the hosted-agent
  Invocations endpoint can forward each one over SSE as soon as it happens.
- Steerable: a user can send additional text while the agent is working
  (``submit_steering_message``). The workflow's single steering checkpoint -
  always before the artifact reaches Blob Storage - asks for HITL
  confirmation (``respond_to_hitl``) before acting on it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from ..config import get_settings
from ..identity import CallerIdentity
from ..models import HitlDecisionRequest, InvocationRequest, OrchestrationStage, StreamEvent
from ..observability import metrics, operation_log_context
from ..storage.metadata_store import MetadataStoreProtocol, get_metadata_store
from .pipeline import ALLOWED_CHECKPOINT_TYPES, build_workflow
from .state import PipelineState, SteeringDecision

logger = logging.getLogger(__name__)

_checkpoint_storage: Any = None


def _get_checkpoint_storage() -> Any:
    global _checkpoint_storage
    if _checkpoint_storage is None:
        settings = get_settings()
        backend = settings.lda_checkpoint_backend
        if backend in ("azurite", "azure"):
            from .table_checkpoint_storage import TableCheckpointStorage

            _checkpoint_storage = TableCheckpointStorage(
                connection_string=settings.azurite_connection_string if backend == "azurite" else None,
                account_url=settings.azure_table_account_url if backend == "azure" else None,
                table_name=settings.lda_checkpoint_table_name,
                allowed_checkpoint_types=ALLOWED_CHECKPOINT_TYPES,
            )
        else:
            from agent_framework import FileCheckpointStorage

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


class OperationNotFoundError(LookupError):
    pass


class OperationAccessDeniedError(PermissionError):
    pass


class OperationNotSteerableError(ValueError):
    """Raised when /steer or /respond is called on an operation that can't accept it
    (already completed/failed/stopped, or - for /respond - not currently paused on a HITL request)."""


async def _require_owned_operation(
    store: MetadataStoreProtocol, operation_id: str, caller: CallerIdentity, *, require_status: str | None = None
):
    operation = await store.get_operation(operation_id)
    if operation is None:
        raise OperationNotFoundError(f"No such operation: {operation_id}")
    if operation["tenant_id"] != caller.tenant_id or operation["user_object_id"] != caller.user_object_id:
        raise OperationAccessDeniedError("This operation was not created by you.")
    if require_status is not None and operation["status"] != require_status:
        raise OperationNotSteerableError(
            f"Operation {operation_id} is not {require_status} (status={operation['status']})."
        )
    return operation


async def check_operation_access(operation_id: str, caller: CallerIdentity, *, require_status: str | None = None):
    """Eager, side-effect-free ownership/status check for the HTTP layer.

    An SSE response can't change its HTTP status once streaming has started, so the
    hosted-agent and broker endpoints call this *before* opening the stream to fail fast
    with a proper 404/403/409. The async generators below re-check the same conditions
    internally, so this call is optional defense-in-depth, not the only place it's enforced.
    """
    return await _require_owned_operation(get_metadata_store(), operation_id, caller, require_status=require_status)


async def submit_steering_message(operation_id: str, caller: CallerIdentity, text: str) -> None:
    """Queues a steering message. Picked up at the workflow's next steering checkpoint -
    never applied immediately, and never redone once the artifact has already been uploaded."""
    store = get_metadata_store()
    operation = await _require_owned_operation(store, operation_id, caller)
    if operation["status"] in ("completed", "failed", "stopped"):
        raise OperationNotSteerableError(
            f"Operation {operation_id} is already {operation['status']}; it can no longer accept new messages."
        )
    await store.queue_steering_message(
        operation_id=operation_id, tenant_id=caller.tenant_id, user_object_id=caller.user_object_id, text=text
    )
    metrics()["steering_messages_total"].inc()


async def run_translation_operation(
    request: InvocationRequest, caller: CallerIdentity
) -> AsyncIterator[StreamEvent]:
    operation_id = request.operation_id or _new_operation_id()
    with operation_log_context(operation_id):
        workflow_name = _workflow_name_for(operation_id)
        store = get_metadata_store()
        checkpoint_storage = _get_checkpoint_storage()

        existing = await store.get_operation(operation_id)

        if existing is not None and (
            existing["tenant_id"] != caller.tenant_id or existing["user_object_id"] != caller.user_object_id
        ):
            raise OperationAccessDeniedError(f"Operation {operation_id} was not created by you.")

        if existing is not None and existing["status"] == "completed":
            async for event in _idempotent_replay(store, existing, _sequencer()):
                yield event
            return

        if existing is not None and existing["status"] == "waiting_hitl":
            raise OperationNotSteerableError(
                f"Operation {operation_id} is waiting on a HITL response; "
                "use POST /invocations/{operation_id}/respond instead."
            )

        await store.start_operation(
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

        if resume_checkpoint_id:
            stream = workflow.run(
                checkpoint_id=resume_checkpoint_id, checkpoint_storage=checkpoint_storage, stream=True
            )
        else:
            metrics()["operations_started"].inc()
            initial_state = PipelineState(
                operation_id=operation_id,
                tenant_id=caller.tenant_id,
                user_object_id=caller.user_object_id,
                prompt=request.prompt,
            )
            stream = workflow.run(initial_state, stream=True, checkpoint_storage=checkpoint_storage)

        async for event in _drive_stream(stream, store, operation_id, _sequencer()):
            yield event


async def respond_to_hitl(
    operation_id: str, caller: CallerIdentity, decision_request: HitlDecisionRequest
) -> AsyncIterator[StreamEvent]:
    with operation_log_context(operation_id):
        store = get_metadata_store()
        checkpoint_storage = _get_checkpoint_storage()
        operation = await _require_owned_operation(store, operation_id, caller, require_status="waiting_hitl")
        if not operation["pending_request_id"]:
            raise OperationFailedError(f"Operation {operation_id} has no pending HITL request id recorded.")

        workflow_name = operation["workflow_name"]
        request_id = operation["pending_request_id"]
        # Clear waiting_hitl before resuming (not after): a concurrent /respond call for the same
        # operation_id - a double-click, a client retry - would otherwise still see require_status=
        # "waiting_hitl" satisfied and resume the same checkpoint a second time.
        await store.mark_in_progress(operation_id)
        metrics()["waiting_hitl_gauge"].dec()
        latest = await checkpoint_storage.get_latest(workflow_name=workflow_name)
        if latest is None:
            raise OperationFailedError(f"No checkpoint found to resume operation {operation_id}.")

        decision = SteeringDecision(action=decision_request.decision, edited_text=decision_request.edited_text)
        workflow = build_workflow(workflow_name=workflow_name, checkpoint_storage=checkpoint_storage)
        stream = workflow.run(
            checkpoint_id=latest.checkpoint_id,
            responses={request_id: decision},
            checkpoint_storage=checkpoint_storage,
            stream=True,
        )

        async for event in _drive_stream(stream, store, operation_id, _sequencer()):
            yield event


def _sequencer():
    sequence = 0

    def next_event(event: str, stage: OrchestrationStage, data: dict) -> StreamEvent:
        nonlocal sequence
        sequence += 1
        return StreamEvent(event=event, stage=stage, data=data, sequence=sequence)

    return next_event


async def _idempotent_replay(store: MetadataStoreProtocol, existing, next_event) -> AsyncIterator[StreamEvent]:
    artifact = await store.get_artifact(existing["artifact_id"])
    if artifact is None:
        yield next_event("error", OrchestrationStage.FAILED, {"message": "Artifact record is missing."})
        return
    if artifact.status != "active":
        # Already swept by the TTL sweeper (cleanup.py) - reporting "completed" with a link
        # here would be a lie: the blob is gone and the broker would just 404 it.
        yield next_event(
            "error",
            OrchestrationStage.FAILED,
            {"message": "This artifact has expired and is no longer available for download."},
        )
        return

    from ..broker.tokens import build_download_link

    download_url, expires_at = build_download_link(
        artifact_id=artifact.artifact_id, tenant_id=artifact.tenant_id, user_object_id=artifact.user_object_id
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


async def _drive_stream(stream, store: MetadataStoreProtocol, operation_id: str, next_event) -> AsyncIterator[StreamEvent]:
    """Iterates a workflow run/resume stream, converting events to StreamEvents and
    updating operation state once the run reaches a pause, a completion, or a failure."""
    pending_request_id: str | None = None
    try:
        async for event in stream:
            mapped = _map_workflow_event(event, next_event)
            if mapped is not None:
                yield mapped
            if getattr(event, "type", None) == "request_info":
                pending_request_id = event.request_id
                yield next_event(
                    "hitl_request",
                    OrchestrationStage.HITL_PENDING,
                    {
                        "request_id": event.request_id,
                        **_model_to_dict(event.data),
                    },
                )

        result = await stream.get_final_response()
        outputs = result.get_outputs()

        if not outputs:
            if pending_request_id is not None:
                await store.set_waiting_on_hitl(operation_id, request_id=pending_request_id)
                metrics()["waiting_hitl_gauge"].inc()
                return
            raise OperationFailedError("Workflow paused without a pending request or a produced output.")

        final_state: PipelineState = outputs[0]
        if final_state.download_url:
            operation = await store.get_operation(operation_id)
            await store.complete_operation(operation_id, artifact_id=final_state.artifact_id)
            metrics()["operations_completed"].inc()
            if operation is not None:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(operation["created_at"])).total_seconds()
                metrics()["operation_duration_seconds"].observe(max(elapsed, 0))
            yield next_event("completed", OrchestrationStage.COMPLETED, {"success": True})
        else:
            await store.stop_operation(operation_id)
            metrics()["operations_stopped"].inc()
            yield next_event("stopped", OrchestrationStage.STOPPED, {"success": False})

    except Exception as exc:  # noqa: BLE001 - reported to the caller, then logged
        logger.exception("Operation %s failed", operation_id)
        await store.fail_operation(operation_id, error=str(exc))
        metrics()["operations_failed"].inc()
        yield next_event("error", OrchestrationStage.FAILED, {"message": str(exc)})


def _model_to_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return {"value": value}


def _map_workflow_event(event, next_event) -> StreamEvent | None:
    if getattr(event, "type", None) != "data" or not isinstance(event.data, dict):
        return None
    data = dict(event.data)
    kind = data.pop("kind", "status")
    stage_value = data.pop("stage", OrchestrationStage.STARTED.value)
    stage = OrchestrationStage(stage_value)
    return next_event(kind, stage, data)


def _new_operation_id() -> str:
    return str(uuid.uuid4())
