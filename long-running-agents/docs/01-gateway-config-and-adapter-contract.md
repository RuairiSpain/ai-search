# Gateway Configuration and Upstream Adapter Contract

**T1 is not configured here.** `apps:`/`upstreams:` below only ever declare
`tier: t2` or `tier: t3` — see `00-tier-model-and-concepts.md` for why. A T1
prompt agent is reached through Foundry's own native incoming A2A endpoint,
not through this file.

The gateway's client-facing A2A surface itself is built on `a2a-sdk`
(`src/gateway/a2a_server/`) rather than the hand-rolled JSON-RPC dispatch
this doc originally specified — see §4 below.

## 1. `apps.yaml` / `upstreams.yaml`

```yaml
auth:
  tenant_id: ${GATEWAY_TENANT_ID}
  # Audience of the GATEWAY's own app registration.
  # NOT https://ai.azure.com — the client authenticates to us, not to Foundry.
  audience: api://a2a-gateway
  # Claim used as the stable subject. oid is immutable per user per tenant.
  # NEVER email/upn/preferred_username: mutable, and addresses get recycled
  # between people. A recycled address is a cross-user data leak.
  subject_claim: oid

apps:
  - name: deep-research
    tier: t3
    upstream: research-pool
    default_mode: long              # client `blocking` may override
    card:
      description: Long-running research agent
      capabilities: { streaming: false, pushNotifications: true }
      # streaming: false — the T3 A2A server pushes via webhook, it does not
      # hold an SSE connection. Advertise this honestly (tier3 doc §4.1).

  - name: ticket-triage
    tier: t2
    upstream: triage-hosted
    default_mode: long
    preview: deny                   # allow | deny (default: deny) — see D10

upstreams:
  - id: triage-hosted
    tier: t2
    project_endpoint: ${FOUNDRY_PROJECT_ENDPOINT}
    agent_name: triage-agent
    identity: per_user              # per_user (default) | service
    # `service` is only for agents with no end user (batch, routine-triggered).
    # It shares one sandbox across all callers. Requires a `justification`
    # field (linter L020) and review. MUST NOT be combined with a
    # UserEntraToken connection (linter L024, tier3 doc §3.3) — that
    # combination is a scheduled job reading across all users under one
    # identity, the isolation hole in permanent form.
    # The preview opt-in header is NOT configurable — it is a platform fact
    # tied to the pinned container protocol version, set by the adapter.

  - id: research-pool
    tier: t3
    protocol: a2a                   # decided: A2A-to-A2A (tier3 doc §4.1)
    instances: [https://rs-1.internal, https://rs-2.internal]
    health: /healthz
    # NOTE: no `affinity` field for T3 — see correction in
    # 00-tier-model-and-concepts.md §2 and tier3 doc §6.1. DTS state lives
    # in the scheduler; any worker resumes any orchestration. Affinity only
    # applies to bring-your-own-compute T3 *without* DTS.
```

One source of truth per concern — the gateway references the Foundry agent
**by name** and restates nothing:

| Concern | Owned by |
|---|---|
| model, instructions, tools, skills, memory | `azure.yaml` / `agents/*.yaml` |
| connections and auth to backing systems | `azure.ai.connection` |
| tier, mode, sync budget, card | gateway `apps:` |
| A2A exposure and progress fidelity | gateway, from `Capabilities` |

```yaml
apps:
  - name: writer
    tier: t2
    upstream: triage-hosted
    foundry_agent: writer        # the name in azure.yaml — not a copy of it
    default_mode: short
    sync_budget_ms: 8000
```

## 2. Adapter contract

```python
# app/upstream/base.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Literal, Protocol


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


class ProgressFidelity(str, Enum):
    COARSE = "coarse"   # state transitions only  (T2 default)
    FINE = "fine"        # per-step narration      (T3 always; T2 opt-in — §5.4 of tier2 doc)


class SteeringMode(str, Enum):
    """Steering is always cooperative and checkpoint-granular. Nothing
    interrupts a model mid-generation."""
    NONE = "none"             # no steering support
    DEFERRED = "deferred"     # queued, applied on the next turn
    CHECKPOINT = "checkpoint" # applied at the next node / step


@dataclass(frozen=True)
class Capabilities:
    progress: ProgressFidelity
    push: bool             # upstream pushes; else gateway polls
    artifacts: bool
    input_required: bool   # can pause for human input
    cancel: bool
    steering: SteeringMode = SteeringMode.NONE


@dataclass(frozen=True)
class Principal:
    """Resolved from the verified inbound token. NEVER from client-supplied data."""
    subject: str            # stable per-user id -> x-ms-user-identity
    tenant: str | None = None

    def user_identity_header(self) -> str:
        # 1-256 chars, [A-Za-z0-9._:@-] only. Reject rather than sanitise:
        # a mangled id silently cross-wires two users into one sandbox.
        if not _USER_ID_RE.fullmatch(self.subject):
            raise ValueError("principal.subject is not a valid x-ms-user-identity")
        return self.subject


@dataclass(frozen=True)
class UpstreamRef:
    """Everything needed to resume. Persisted against contextId / taskId."""
    session_id: str | None = None       # T2 agent_session_id
    conversation_id: str | None = None  # T2 Foundry conversation
    run_id: str | None = None           # T2 response.id | T3 instance id
    container_id: str | None = None     # code interpreter container, if explicit
    instance_url: str | None = None     # T3 worker affinity (BYO-compute only)


@dataclass
class StatusEvent:
    task_id: str
    state: TaskState
    sequence: int          # monotonic per task; dedupe + ordering key
    detail: str | None = None
    final: bool = False


@dataclass
class ArtifactEvent:
    task_id: str
    artifact_id: str
    name: str
    mime: str
    sequence: int
    uri: str | None = None   # by reference. Never inline bytes.


@dataclass
class Submission:
    task_id: str
    context_id: str
    state: TaskState
    ref: UpstreamRef
    inline_result: str | None = None   # set when it finished inside the budget


@dataclass
class SteerResult:
    """Outcome of an interjection. `Queued` is not `Accepted` — the UI must
    distinguish them or users will think the system ignored them."""
    outcome: Literal["accepted", "queued", "unsupported"]
    applies_at: str | None = None      # e.g. "next node", "next turn"
    sequence: int | None = None        # gw_interjection.sequence


class UpstreamAdapter(Protocol):
    """One interface, two implementations. Polling vs pushing is hidden here."""

    capabilities: Capabilities

    async def submit(
        self,
        *,
        app: str,
        principal: Principal,
        ref: UpstreamRef,          # empty on first turn; populated to continue
        text: str,
        blocking: bool,
        budget_ms: int,
    ) -> Submission: ...

    async def follow(
        self,
        ref: UpstreamRef,
        *,
        principal: Principal,
        from_sequence: int = 0,
    ) -> AsyncIterator[StatusEvent | ArtifactEvent]:
        """Async iterator regardless of implementation.

        T2 polls and synthesises events. T3 relays pushed events.
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

        Distinct from `resume()`: that replies to a *paused* task and advances
        it. This adds advisory context to a *running* one and may not take
        effect until the next checkpoint — or at all, if unsupported.

        The caller must surface the result honestly. Reporting `Queued` as
        though it were `Accepted` produces a UI that looks broken.
        """
        ...

    async def cancel(self, ref: UpstreamRef, *, principal: Principal) -> None: ...

    async def artifact_url(
        self, ref: UpstreamRef, artifact_id: str, *, principal: Principal
    ) -> str:
        """Re-signed, short-lived. Never leak the raw upstream URI to clients."""
        ...

    async def health(self) -> bool: ...
```

### Shared base — Foundry Responses polling

T2's adapter is built on a shared base class that owns the actual
Responses-API poll loop, artifact-citation detection, and container-file
fetch logic. It is not registered as a standalone gateway tier — nothing
mounts it directly — but the shape is worth keeping visible here because
it's what T2 subclasses:

```python
class FoundryResponsesAdapter:
    capabilities = Capabilities(
        progress=ProgressFidelity.COARSE, push=False,
        artifacts=True,            # code interpreter container files, ~1h TTL
        input_required=False, cancel=True,
    )

    async def submit(self, *, app, principal, ref, text, blocking, budget_ms):
        conv_id = ref.conversation_id or (
            await self._openai.conversations.create()
        ).id

        resp = await self._openai.responses.create(
            background=not blocking,
            conversation=conv_id,
            input=text,
            extra_body={"agent_reference": {
                "name": self._agent_name, "type": "agent_reference"}},
        )
        return Submission(
            task_id=new_task_id(),
            context_id=conv_id,
            state=_map_state(resp.status),
            ref=UpstreamRef(conversation_id=conv_id, run_id=resp.id),
        )

    async def follow(self, ref, *, principal, from_sequence=0):
        seq = from_sequence
        while True:
            resp = await self._openai.responses.retrieve(ref.run_id)
            state = _map_state(resp.status)
            seq += 1
            yield StatusEvent(task_id=..., state=state, sequence=seq,
                              final=state in _TERMINAL)
            if state in _TERMINAL:
                return
            await asyncio.sleep(self._interval)
```

`_map_state`: `queued -> SUBMITTED`, `in_progress -> WORKING`,
`completed -> COMPLETED`, `failed|incomplete -> FAILED`, `cancelled -> CANCELED`.

Verify the model supports background mode. Without it, calls run
synchronously under a 100-second timeout.

### T2 — hosted agent (the delta)

Identical to the shared base apart from headers and the session id. That
small delta is the whole point of the shared interface.

```python
class FoundryHostedAdapter(FoundryResponsesAdapter):
    capabilities = Capabilities(
        progress=ProgressFidelity.COARSE, push=False, artifacts=True,
        input_required=False, cancel=True,
    )

    # Platform fact, not configuration. Delete this line when the preview
    # flag goes GA rather than editing every upstream entry.
    _PREVIEW = {"Foundry-Features": "HostedAgents=V1Preview"}

    def _headers(self, principal: Principal) -> dict[str, str]:
        h = dict(self._PREVIEW)
        if self._identity_mode == "per_user":
            h["x-ms-user-identity"] = principal.user_identity_header()
        return h

    async def health(self) -> bool:
        """Probe delegation at startup, not at first real user request.

        A missing UserIdentityImpersonation grant is an Azure RBAC fact that
        YAML cannot assert. Assert it here and fail readiness instead.
        """
        if self._identity_mode != "per_user":
            return await self._ping()
        probe = Principal(subject="gateway-readiness-probe")
        try:
            await self._ping(headers=self._headers(probe))
        except Forbidden:
            log.error("upstream %s: gateway identity lacks "
                      "UserIdentityImpersonation; per-user isolation is NOT "
                      "in effect", self._id)
            return False
        return True

    async def submit(self, *, app, principal, ref, text, blocking, budget_ms):
        client = self._project.get_openai_client(agent_name=self._agent_name)
        resp = await client.responses.create(
            background=not blocking,
            conversation=ref.conversation_id,
            input=text,
            extra_headers=self._headers(principal),
        )
        session_id = resp.model_extra.get("agent_session_id")
        return Submission(
            task_id=new_task_id(),
            context_id=ref.conversation_id or resp.conversation.id,
            state=_map_state(resp.status),
            ref=UpstreamRef(
                session_id=session_id,
                conversation_id=resp.conversation.id,
                run_id=resp.id,
            ),
        )

    async def artifact_url(self, ref, artifact_id, *, principal):
        # Session Files API — already identity-scoped upstream.
        return await self._session_files.download_url(
            session_id=ref.session_id,
            path=artifact_id,
            headers=self._headers(principal),
        )
```

Full identity delegation reference implementation (principal extraction,
JWT validation, IDOR test suite) is in `05-tier2-hosted-agents.md`.

### T3 — MAF + Durable Task

```python
class DurableAdapter:
    capabilities = Capabilities(
        progress=ProgressFidelity.FINE, push=True, artifacts=True,
        input_required=True, cancel=True,
    )
```

`submit` starts the orchestration. `follow` relays pushed events from the
gateway's webhook callback table (T3 does not hold an SSE connection — see
tier3 doc §4.1). `resume` maps an A2A `input-required` reply onto
`client.raise_event(instance_id, "APPROVAL", payload)`.

Two constraints from the durable layer:

- Streaming is request/response underneath; token streaming needs a side
  channel (Redis Stream or equivalent) if you ever want it. Fine-grained
  *progress* is delivered via the same `gw.progress.v1` push-activity
  pattern as T2, not via a stream.
- Durable Task Scheduler caps entity state at 1 MB. Artifacts must be blob
  URIs in session state, never inline. Reinforces `ArtifactEvent.uri`.

## 3. Version pins

Every one of these is preview or recently breaking. One file, reviewed
monthly.

| Package | Pin | Why it matters |
|---|---|---|
| `azure-ai-projects` | `>=2.0.0` | `create_agent()` removed; `create_version()` replaces it |
| `azure-ai-agentserver-core` | `>=2.0.0b7` | container protocol 2.0.0; 1.0.0 blocked after 31 Jul 2026 |
| `a2a-sdk` | pinned | `DatabaseTaskStore`, `TaskUpdater`; API has moved repeatedly |
| `agent-framework-durabletask` | `--pre` | T3, preview |
| `agent-framework-a2a` | `--pre` | T3 A2A server side, preview |
| `agent-framework-azurefunctions` | `--pre` | T3 on Flex Consumption |
| `agent-framework-foundry` | prerelease | T2 `FoundryChatClient`, `ResponsesHostServer` |

Do not use the Assistants API (threads / runs / messages) anywhere. It
retires **26 August 2026** and does not support incoming A2A. Blocked always
by the linter (`L032`, see D6 in `02-decisions.md`).

## 4. The gateway's own A2A surface: `a2a-sdk`

The gateway's client-facing A2A surface (`message/send`, `tasks/get`,
`tasks/cancel`, streaming) is built on `a2a-sdk`'s server-side pieces
(`src/gateway/a2a_server/`), not a hand-rolled JSON-RPC dispatch. The SDK's
actual v1.1.2 API differs substantially from what an earlier draft of this
project sketched (it's protobuf-based: `a2a.types.a2a_pb2`, and route
mounting goes through `DefaultRequestHandler` + route-builder functions —
`create_agent_card_routes`, `create_jsonrpc_routes`, `create_rest_routes` —
not a single `A2AFastAPIApplication` class). Verify against the installed
package before writing integration code; do not trust an SDK code snippet
you haven't run.

What this buys, concretely:

- **Conformant wire format.** JSON-RPC method names, `Task`/`Message`/`Part`
  shapes, and state vocabulary come from the SDK, not a bespoke schema — a
  generic A2A client can talk to this gateway.
- **`TaskStore` as the enforcement seam.** The gateway implements its own
  `TaskStore` (`GatewayTaskStoreAdapter`) rather than adopting the SDK's own
  `DatabaseTaskStore` — `gw_task`/`gw_context` stay the one system of
  record, and `TaskStore.get()` is where D1's IDOR control actually lives:
  a task whose context isn't the calling principal's own returns `None`
  ("not found"), never "forbidden."
- **`AgentExecutor` as the adapter bridge.** `GatewayAgentExecutor` wraps
  the existing `UpstreamAdapter` protocol (§2 above) — `submit`/`follow`/
  `resume`/`cancel` don't change shape, they're just driven from a
  different caller.

Three integration details worth carrying forward, each found only by
running the thing against a real client, not by reading the SDK's source:

- **`A2A-Version` header.** The SDK's own request-version validator reads
  `context.state["headers"]`; a `ServerCallContextBuilder` that doesn't
  populate it gets every request treated as protocol 0.3 and rejected. Any
  custom `ServerCallContextBuilder` must set `state["headers"]` itself —
  it is not free from `ServerCallContext(state={...})`.
- **Cancellation ordering.** `ActiveTask.cancel()` force-cancels the
  running `AgentExecutor.execute()` coroutine *before* awaiting
  `AgentExecutor.cancel()`. A design that expects the original `follow()`
  loop to observe and persist the upstream's cancellation confirmation
  never gets the chance — that coroutine is already gone. `cancel()` must
  persist the terminal state itself, directly against the store, not via
  the event queue the executor also owns (writing to a queue that the
  producer's own `finally` is concurrently closing silently drops the
  event).
- **Per-request task identity, not per-message.** `RequestContextBuilder`
  mints a fresh `task_id` on every `message/send` whose message omits one —
  there is no built-in way to redirect that request's `ActiveTask` to a
  *different*, already-existing task after the fact. A client-side
  "blind retry" (same `messageId`, no `taskId`, because the original
  response was lost) can't be transparently resolved to the original task
  under this model. The gateway still dedupes the *upstream* submission
  (D7 — the retry never re-triggers `adapter.submit()`), but the retry
  itself is rejected with a clear error rather than silently misrouted.
  Clients that need idempotent retries should supply their own `taskId` up
  front, which routes a retry through the resume path instead.
