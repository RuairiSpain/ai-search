"""GatewayAgentExecutor: bridges a2a-sdk's AgentExecutor interface to our
own UpstreamAdapter (T2/T3 only — see docs/00 §4). Every control that used
to live in the hand-rolled api/a2a.py dispatch now lives here, since this
is the sole remaining entry point client requests reach an adapter through:

  * D1 IDOR control — context ownership via get_or_create_context /
    authorise_context, never a bare lookup.
  * D7 submit idempotency — dedupe_inbound() before the upstream call.
  * D7 "never optimistic" cancellation — state only changes when the
    upstream confirms, via the follow() loop, not here.

Inbound file parts (`Part.raw` / `Part.url`) are extracted alongside text
and handed to the adapter as `InboundFile`s (docs/01 §4 "Bidirectional
files") — upload/relay is adapter-specific (T2 uploads via the Files API,
T3 relays the part as-is), so this module only extracts and passes them
through, never touches bytes itself. Steering into a `working` task and
T2's resume() are still open — see docs/08-open-items-and-experiments.md.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import replace

from a2a.helpers.proto_helpers import get_text_parts, new_text_message, new_url_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types.a2a_pb2 import TaskState as SdkTaskState
from a2a.utils.errors import InvalidParamsError, UnsupportedOperationError

from gateway.a2a_server.context import principal_from
from gateway.artifacts import ArtifactHarvester
from gateway.auth.principal import Principal
from gateway.store.context_store import ContextStore
from gateway.store.task_store import TaskStore as GwTaskStore
from gateway.upstream.base import (
    ArtifactEvent,
    InboundFile,
    StatusEvent,
    UpstreamAdapter,
    UpstreamRef,
)
from gateway.upstream.base import (
    TaskState as GwTaskState,
)

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


class GatewayAgentExecutor(AgentExecutor):
    def __init__(
        self,
        *,
        app: str,
        tier: str,
        adapter: UpstreamAdapter,
        contexts: ContextStore,
        tasks: GwTaskStore,
        harvester: ArtifactHarvester,
        default_blocking: bool,
        budget_ms: int,
        lease_seconds: int,
    ):
        self._app = app
        self._tier = tier
        self._adapter = adapter
        self._contexts = contexts
        self._tasks = tasks
        self._harvester = harvester
        self._default_blocking = default_blocking
        self._budget_ms = budget_ms
        self._lease_seconds = lease_seconds

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        principal = principal_from(context.call_context)

        if context.current_task is not None:
            await self._continue_existing(context, event_queue, principal)
            return

        ctx_row = await self._contexts.get_or_create_context(
            context.context_id, self._app, principal
        )

        message_id = context.message.message_id if context.message else None
        if message_id:
            fresh = await self._tasks.dedupe_inbound(message_id)
            if not fresh:
                await self._relay_deduped_retry(message_id)
                return

        await self._tasks.create_task(
            task_id=context.task_id,
            context_id=ctx_row.context_id,
            app=self._app,
            tier=self._tier,
            state=GwTaskState.SUBMITTED,
            run_id=None,
        )
        await self._tasks.renew_lease(context.task_id, self._lease_seconds)
        if message_id:
            await self._tasks.link_inbound_message(message_id, context.task_id)

        # Deliberately no explicit `new_task()` enqueue here: the
        # create_task() call above already persists the gw_task row, which
        # is all a2a-sdk's TaskManager actually requires before accepting a
        # TaskStatusUpdateEvent as the task's first event (verified against
        # the installed a2a-sdk: `_handle_task_modification_event` only
        # checks that a row exists, not that a `Task` event preceded it).
        # Enqueueing our own `new_task()` on top of that INSERT made the SDK
        # treat it as a duplicate creation ("Task already exists, ignoring
        # task replacement") on every single send — noisy and redundant,
        # not a real conflict.
        updater = TaskUpdater(event_queue, context.task_id, ctx_row.context_id)

        text = _extract_text(context)
        files = _extract_files(context)
        submission = await self._adapter.submit(
            app=self._app,
            principal=principal,
            ref=ctx_row.upstream_ref(),
            text=text,
            files=files,
            blocking=self._default_blocking,
            budget_ms=self._budget_ms,
        )
        await self._tasks.set_run_id(context.task_id, submission.ref.run_id)
        _, won = await self._contexts.record_upstream_ref(
            ctx_row.context_id, principal, submission.ref
        )
        if not won:
            await self._terminate_orphaned_session(ctx_row.context_id, submission.ref)

        await updater.start_work()
        await self._follow_and_relay(
            task_id=context.task_id,
            context_id=ctx_row.context_id,
            ref=submission.ref,
            principal=principal,
            updater=updater,
        )

    async def _terminate_orphaned_session(self, context_id: str, ref: UpstreamRef) -> None:
        """The upstream session/instance this request just created lost
        the session-creation race (docs/05 §6.3) — some concurrent request
        for the same context won and its ref is now the one of record.
        Attempt to actually terminate the orphan rather than just leak it,
        via the adapter's own optional `terminate_session` hook (duck-typed
        the same way `fetch_artifact_bytes` is — not every tier has
        anything to terminate; T3 instances aren't a race-prone resource
        the way a T2 session is)."""
        terminate = getattr(self._adapter, "terminate_session", None)
        if terminate is None or not ref.session_id:
            log.warning(
                "session-creation race on context %s: discarding the upstream "
                "session this request just created (docs/05 §6.3) -- no "
                "terminate_session hook available for this tier, so it will "
                "leak until reclaimed some other way",
                context_id,
            )
            return
        try:
            await terminate(ref.session_id)
            log.warning(
                "session-creation race on context %s: terminated the orphaned "
                "upstream session %s this request just created (docs/05 §6.3)",
                context_id,
                ref.session_id,
            )
        except Exception:
            log.exception(
                "session-creation race on context %s: failed to terminate "
                "orphaned session %s -- it will leak until reclaimed some "
                "other way",
                context_id,
                ref.session_id,
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        principal = principal_from(context.call_context)
        task_row = await self._tasks.get_task(context.task_id)
        if task_row is None:
            return
        ctx_row = await self._contexts.authorise_context(task_row.context_id, principal)
        if ctx_row is None:
            return
        ref = replace(ctx_row.upstream_ref(), run_id=task_row.run_id)
        await self._adapter.cancel(ref, principal=principal)
        # D7 "never optimistic" doesn't mean "never here": it means never
        # flip state before the upstream confirms. The confirmation is
        # adapter.cancel() returning successfully above -- we can't instead
        # wait for _follow_and_relay()'s loop to observe it, because the SDK
        # force-cancels this task's producer coroutine around this same call
        # (verified against the installed a2a-sdk: ActiveTask.cancel() calls
        # producer_task.cancel() before awaiting AgentExecutor.cancel()), so
        # that loop never runs again after this point.
        #
        # Writing through TaskUpdater/event_queue here is unreliable for the
        # same reason: the producer's own `finally` is concurrently closing
        # that exact queue, and an event enqueued into a closing queue is
        # silently dropped (verified: "Queue was closed during enqueuing.
        # Event dropped." from a2a-sdk's own event_queue_v2). Write directly
        # to our store instead, same as every other direct mutation in this
        # class (create_task, set_run_id).
        await self._tasks.append_event(
            context.task_id,
            task_row.last_sequence + 1,
            "status",
            {"state": GwTaskState.CANCELED.value, "final": True},
        )

    async def _continue_existing(
        self, context: RequestContext, event_queue: EventQueue, principal: Principal
    ) -> None:
        task = context.current_task
        assert task is not None
        task_row = await self._tasks.get_task(task.id)
        if task_row is None:
            raise UnsupportedOperationError
        ctx_row = await self._contexts.authorise_context(task_row.context_id, principal)
        if ctx_row is None:
            raise UnsupportedOperationError
        ref = replace(ctx_row.upstream_ref(), run_id=task_row.run_id)
        updater = TaskUpdater(event_queue, task.id, ctx_row.context_id)

        if task.status.state == SdkTaskState.TASK_STATE_INPUT_REQUIRED:
            text = _extract_text(context)
            files = _extract_files(context)
            submission = await self._adapter.resume(
                ref, principal=principal, text=text, files=files
            )
            await self._tasks.set_run_id(task.id, submission.ref.run_id)
            await self._tasks.renew_lease(task.id, self._lease_seconds)
            await updater.start_work()
            await self._follow_and_relay(
                task_id=task.id,
                context_id=ctx_row.context_id,
                ref=submission.ref,
                principal=principal,
                updater=updater,
            )
            return

        # A2A does not define client-initiated messages against a task
        # that's still `working` (docs/02-decisions.md D7, verified against
        # the current spec). Reject cleanly rather than silently no-op or
        # misroute into submit(). Steering has its own tracked gap.
        raise UnsupportedOperationError

    async def _follow_and_relay(
        self,
        *,
        task_id: str,
        context_id: str,
        ref: UpstreamRef,
        principal: Principal,
        updater: TaskUpdater,
    ) -> None:
        fetch_bytes = getattr(self._adapter, "fetch_artifact_bytes", None)
        events: AsyncIterator[StatusEvent | ArtifactEvent] = self._adapter.follow(
            ref, task_id=task_id, principal=principal, from_sequence=0
        )
        async for event in events:
            # Heartbeat: a lease only expires once events genuinely stop
            # arriving, not on a fixed clock unrelated to actual upstream
            # progress. Every event relayed here -- status or artifact --
            # pushes gw_task_reaper's deadline back out.
            await self._tasks.renew_lease(task_id, self._lease_seconds)
            if isinstance(event, ArtifactEvent):
                if event.uri is None and fetch_bytes is not None:
                    event = await self._harvester.harvest(
                        event,
                        app=self._app,
                        principal=principal,
                        context_id=context_id,
                        fetch_bytes=fetch_bytes,
                    )
                if event.uri:
                    await updater.add_artifact(
                        parts=[new_url_part(event.uri, media_type=event.mime, filename=event.name)],
                        artifact_id=event.artifact_id,
                        name=event.name,
                    )
                continue
            # `event.detail` is the gw.progress.v1 narration text (docs/05
            # §5.4, docs/06 §5.4) -- T2's own poll loop never populates it
            # (FoundryResponsesAdapter.follow() has no narration source),
            # but T3's DurableAdapter does, relayed here via `message` so it
            # actually reaches the wire instead of only ever updating
            # `state`. Previously dropped entirely: this call passed no
            # `message`, so a client had no way to distinguish a narrating
            # upstream from a silent one -- same event vocabulary, same
            # code path, but only T3 ever has anything to put in `detail`.
            message = new_text_message(event.detail) if event.detail else None
            await updater.update_status(_GW_TO_SDK_STATE[event.state], message=message)

    async def _relay_deduped_retry(self, message_id: str) -> None:
        """A retry of a message we've already accepted (D7 "submit
        idempotency"). The upstream call is never repeated — that's the
        property that actually matters, since it's what avoids a duplicate
        agent run / duplicate billing, and dedupe_inbound() already
        guaranteed it before this is ever called.

        What this can't do is hand the retry back the ORIGINAL task_id:
        a2a-sdk mints a fresh task_id per request whenever the client's
        message omits one, and TaskManager rejects a Task/event whose id
        doesn't match the id it was constructed with (verified against the
        installed a2a-sdk) — there's no supported way to redirect this
        request's ActiveTask to a different id after the fact. A client
        that wants the durable task back should resend message/send
        including the taskId once it knows it (routes through
        `_continue_existing` instead) or just call tasks/get with it.
        """
        linked_task_id = await self._tasks.get_linked_task_id(message_id)
        log.warning(
            "message %s already handled by task %s; a retry with no taskId "
            "can't be redirected to it under a2a-sdk's per-request task "
            "identity — rejecting rather than double-submitting or "
            "enqueueing a mismatched task id",
            message_id,
            linked_task_id,
        )
        raise InvalidParamsError(
            message="This message was already submitted. Retry tasks/get, or "
            "resend message/send including the taskId from the original response."
        )


def _extract_text(context: RequestContext) -> str:
    if context.message is None:
        return ""
    return "\n".join(get_text_parts(context.message.parts))


def _extract_files(context: RequestContext) -> list[InboundFile]:
    """`Part`'s four content variants (`text`/`raw`/`url`/`data`) are one
    proto oneof, so `HasField` is the correct way to tell them apart —
    truthy checks on `raw`/`url` would misfire on an explicitly-empty-but-
    present value. `data` (a structured payload, A2A's DataPart equivalent)
    is deliberately not treated as a file here; it's a different concern."""
    if context.message is None:
        return []
    files: list[InboundFile] = []
    for part in context.message.parts:
        if part.HasField("raw"):
            files.append(
                InboundFile(
                    name=part.filename or "file",
                    mime=part.media_type or "application/octet-stream",
                    data=part.raw,
                )
            )
        elif part.HasField("url"):
            files.append(
                InboundFile(
                    name=part.filename or "file",
                    mime=part.media_type or "application/octet-stream",
                    url=part.url,
                )
            )
    return files
