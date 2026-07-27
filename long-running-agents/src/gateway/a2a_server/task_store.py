"""Projects gw_context/gw_task/gw_artifact into a2a-sdk's `TaskStore`
interface. Deliberately NOT a2a-sdk's own `DatabaseTaskStore`: gw_task and
gw_context are already the system of record and already enforce D1's IDOR
control (docs/02-decisions.md D1). Running a second, SDK-native schema
alongside ours would either duplicate that enforcement or create a second,
unenforced copy of task state. One system of record, one place the IDOR
check lives.

`get()` is where D1 actually gets enforced for this surface: it returns
None — "not found", never "forbidden" — for a task_id that exists but
belongs to a different principal, exactly the posture the old hand-rolled
`authorise_context` had (docs/05 §3.5 "404, not 403 — don't confirm the
id exists").
"""
from __future__ import annotations

import logging

from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore as SdkTaskStoreInterface
from a2a.types.a2a_pb2 import (
    Artifact,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    Task,
    TaskStatus,
)
from a2a.types.a2a_pb2 import (
    TaskState as SdkTaskState,
)
from a2a.utils.errors import UnsupportedOperationError

from gateway.a2a_server.context import principal_from
from gateway.artifacts import ArtifactHarvester
from gateway.store.artifact_store import ArtifactStore
from gateway.store.context_store import ContextStore
from gateway.store.message_store import MessageStore
from gateway.store.task_store import TaskStore as GwTaskStore
from gateway.upstream.base import TaskState as GwTaskState

log = logging.getLogger(__name__)

_GW_TO_SDK_STATE: dict[GwTaskState, SdkTaskState] = {
    GwTaskState.SUBMITTED: SdkTaskState.TASK_STATE_SUBMITTED,
    GwTaskState.WORKING: SdkTaskState.TASK_STATE_WORKING,
    GwTaskState.INPUT_REQUIRED: SdkTaskState.TASK_STATE_INPUT_REQUIRED,
    GwTaskState.COMPLETED: SdkTaskState.TASK_STATE_COMPLETED,
    GwTaskState.FAILED: SdkTaskState.TASK_STATE_FAILED,
    GwTaskState.CANCELED: SdkTaskState.TASK_STATE_CANCELED,
    GwTaskState.REJECTED: SdkTaskState.TASK_STATE_REJECTED,
    GwTaskState.AUTH_REQUIRED: SdkTaskState.TASK_STATE_AUTH_REQUIRED,
}
_SDK_TO_GW_STATE = {v: k for k, v in _GW_TO_SDK_STATE.items()}


class GatewayTaskStoreAdapter(SdkTaskStoreInterface):
    def __init__(
        self,
        *,
        gw_tasks: GwTaskStore,
        gw_contexts: ContextStore,
        gw_artifacts: ArtifactStore,
        gw_messages: MessageStore,
        harvester: ArtifactHarvester,
    ):
        self._gw_tasks = gw_tasks
        self._gw_contexts = gw_contexts
        self._gw_artifacts = gw_artifacts
        self._gw_messages = gw_messages
        self._harvester = harvester

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        principal = principal_from(context)
        task_row = await self._gw_tasks.get_task(task_id)
        if task_row is None:
            return None
        # THE IDOR control for this surface: a task whose context isn't
        # this principal's own is treated identically to a nonexistent one.
        ctx_row = await self._gw_contexts.authorise_context(task_row.context_id, principal)
        if ctx_row is None:
            return None

        artifacts = await self._project_artifacts(task_id)
        history, status_message = await self._project_messages(
            task_id, task_row.current_message_id
        )
        status = TaskStatus(
            state=_GW_TO_SDK_STATE.get(GwTaskState(task_row.state), SdkTaskState.TASK_STATE_UNSPECIFIED)
        )
        if status_message is not None:
            status.message.CopyFrom(status_message)
        return Task(
            id=task_row.task_id,
            context_id=task_row.context_id,
            status=status,
            artifacts=artifacts,
            history=history,
        )

    async def save(self, task: Task, context: ServerCallContext) -> None:
        """The SDK calls this on every Task/TaskStatusUpdateEvent/
        TaskArtifactUpdateEvent the executor enqueues (verified against
        a2a-sdk's TaskManager.save_task_event -> _save_task), always with
        the full, already-merged Task object — so this is a pure state
        mirror, not a delta merge. The row itself must already exist:
        GatewayAgentExecutor.execute() creates it (with app/tier, which
        aren't part of the generic Task schema) before enqueueing
        anything, so this never needs to INSERT.
        """
        task_row = await self._gw_tasks.get_task(task.id)
        if task_row is None:
            log.error(
                "TaskStore.save() called for task %s with no gw_task row; "
                "the executor must create it before enqueueing any event",
                task.id,
            )
            return
        gw_state = _SDK_TO_GW_STATE.get(task.status.state, GwTaskState.WORKING)
        next_sequence = task_row.last_sequence + 1
        await self._gw_tasks.append_event(
            task.id,
            next_sequence,
            "status",
            {"state": gw_state.value, "final": gw_state.value in {"completed", "failed", "canceled", "rejected"}},
        )

        # a2a-sdk's TaskManager hands this method the full, already-merged
        # history + current status.message on every call (see this class's
        # docstring above) -- persist both, and record which message is
        # "current" so get() can split them back apart correctly (docs/08
        # item 17: a completed task's answer lives in status.message and
        # is never later demoted into history, since no further save()
        # call happens after a terminal state).
        messages = list(task.history)
        current_message_id = None
        if task.status.HasField("message"):
            messages.append(task.status.message)
            current_message_id = task.status.message.message_id
        await self._gw_messages.append_messages(task.id, messages)
        await self._gw_tasks.set_current_message_id(task.id, current_message_id)

    async def list(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        # Best-effort: no pagination yet (page_size/page_token ignored).
        # Good enough for "what's running in this context" right now;
        # revisit if a client actually needs to page through history.
        principal = principal_from(context)
        if not params.context_id:
            return ListTasksResponse(tasks=[])
        ctx_row = await self._gw_contexts.authorise_context(params.context_id, principal)
        if ctx_row is None:
            return ListTasksResponse(tasks=[])
        tasks = []
        for task_id in await self._gw_tasks.list_task_ids_for_context(params.context_id):
            task = await self.get(task_id, context)
            if task is not None:
                tasks.append(task)
        return ListTasksResponse(tasks=tasks, total_size=len(tasks))

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        # Retention is time-based (D5), not ad-hoc client deletion.
        raise UnsupportedOperationError

    async def _project_artifacts(self, task_id: str) -> list[Artifact]:
        rows = await self._gw_artifacts.list_for_task(task_id)
        artifacts: list[Artifact] = []
        for row in rows:
            if row.blob_key is None:
                continue
            # Fresh SAS minted on every read, never persisted — a URL
            # embedded in a Task read long after harvest must still work
            # (docs/07 §2 item 4).
            url = await self._harvester.download_url(row.blob_key)
            artifacts.append(
                Artifact(
                    artifact_id=row.artifact_id,
                    name=row.name,
                    parts=[Part(url=url, filename=row.name, media_type=row.mime)],
                )
            )
        return artifacts

    async def _project_messages(
        self, task_id: str, current_message_id: str | None
    ) -> tuple[list[Message], Message | None]:
        """Splits persisted messages back into (history, status.message),
        mirroring the invariant a2a-sdk's TaskManager maintains in memory:
        `history` is every message once superseded, `status.message` is the
        current one. `current_message_id` (persisted by save(), see above)
        is what makes this a lookup rather than a guess -- a bare
        "last row wins" heuristic would be wrong the moment a later status
        update carries no message of its own (narration disappears, nothing
        replaces it, so nothing should be treated as "current")."""
        messages = await self._gw_messages.list_for_task(task_id)
        if current_message_id and messages and messages[-1].message_id == current_message_id:
            return messages[:-1], messages[-1]
        return messages, None
