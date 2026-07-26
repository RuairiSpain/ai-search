"""The adapter contract. One interface, three implementations (T1/T2/T3) —
polling vs. pushing is hidden behind it. This is the churn firewall: the
rest of the gateway never imports an Azure package, only these types.

Full narrative: docs/01-gateway-config-and-adapter-contract.md §2.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

from gateway.auth.principal import Principal


class TaskState(str, Enum):
    """A2A vocabulary. Adopting the protocol later is a rename, not a redesign."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"


TERMINAL_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED}
)


class ProgressFidelity(str, Enum):
    COARSE = "coarse"  # state transitions only  (T1, T2 default)
    FINE = "fine"  # per-step narration      (T3 always; T2 opt-in — gw.progress.v1)


class SteeringMode(str, Enum):
    """Steering is always cooperative and checkpoint-granular. Nothing
    interrupts a model mid-generation."""

    NONE = "none"  # T1 single response
    DEFERRED = "deferred"  # queued, applied on the next turn
    CHECKPOINT = "checkpoint"  # applied at the next node / step


@dataclass(frozen=True)
class Capabilities:
    progress: ProgressFidelity
    push: bool  # upstream pushes; else gateway polls
    artifacts: bool
    input_required: bool  # can pause for human input
    cancel: bool
    steering: SteeringMode = SteeringMode.NONE


@dataclass(frozen=True)
class UpstreamRef:
    """Everything needed to resume. Persisted against contextId / taskId."""

    session_id: str | None = None  # T2 agent_session_id
    conversation_id: str | None = None  # T1/T2 Foundry conversation
    run_id: str | None = None  # T1/T2 response.id | T3 instance id
    container_id: str | None = None  # code interpreter container, if explicit
    instance_url: str | None = None  # T3 worker affinity (BYO-compute only)


@dataclass
class StatusEvent:
    task_id: str
    state: TaskState
    sequence: int  # monotonic per task; dedupe + ordering key
    detail: str | None = None
    final: bool = False


@dataclass
class ArtifactEvent:
    task_id: str
    artifact_id: str
    name: str
    mime: str
    sequence: int
    uri: str | None = None  # by reference. Never inline bytes.


@dataclass
class Submission:
    task_id: str
    context_id: str
    state: TaskState
    ref: UpstreamRef
    inline_result: str | None = None  # set when it finished inside the budget


@dataclass
class SteerResult:
    """Outcome of an interjection. `Queued` is not `Accepted` — the UI must
    distinguish them or users will think the system ignored them."""

    outcome: Literal["accepted", "queued", "unsupported"]
    applies_at: str | None = None  # e.g. "next node", "next turn"
    sequence: int | None = None  # gw_interjection.sequence


class UpstreamAdapter(Protocol):
    """One interface, three implementations. Polling vs pushing is hidden here."""

    capabilities: Capabilities

    async def submit(
        self,
        *,
        app: str,
        principal: Principal,
        ref: UpstreamRef,  # empty on first turn; populated to continue
        text: str,
        blocking: bool,
        budget_ms: int,
    ) -> Submission: ...

    def follow(
        self,
        ref: UpstreamRef,
        *,
        principal: Principal,
        from_sequence: int = 0,
    ) -> AsyncIterator[StatusEvent | ArtifactEvent]:
        """Async iterator regardless of implementation.

        T1/T2 poll and synthesise events. T3 relays pushed events.
        `from_sequence` makes reconnection resumable.
        """
        ...

    async def resume(
        self, ref: UpstreamRef, *, principal: Principal, text: str
    ) -> Submission:
        """Reply to an input-required pause."""
        ...

    async def steer(
        self, ref: UpstreamRef, *, principal: Principal, text: str
    ) -> SteerResult:
        """Interject into a task that is still `working`.

        Distinct from `resume()`: that replies to a *paused* task and
        advances it. This adds advisory context to a *running* one and may
        not take effect until the next checkpoint — or at all, if
        unsupported. The security envelope (user-role only, no urgency
        markers, advisory framing) lives in the caller — see
        docs/02-decisions.md D7.
        """
        ...

    async def cancel(self, ref: UpstreamRef, *, principal: Principal) -> None: ...

    async def artifact_url(
        self, ref: UpstreamRef, artifact_id: str, *, principal: Principal
    ) -> str:
        """Re-signed, short-lived. Never leak the raw upstream URI to clients."""
        ...

    async def health(self) -> bool: ...
