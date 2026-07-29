# Chat surfaces: streaming progress and OBO identity

This agent's transport contract is plain SSE over `POST /invocations`
(`event: status` / `event: artifact` / `event: completed`). How each Microsoft chat surface
gets progress out of that stream - and how the caller's identity reaches `identity.py` -
differs per surface.

## Microsoft Teams (custom app)

Easiest option. A Teams tab or bot backend can consume the SSE stream directly, or relay it
over a WebSocket/SignalR connection to the Teams client and render each status as a typing
indicator, an updated Adaptive Card, or a sequence of chat messages:

```json
{"type": "status", "message": "Agent working..."}
{"type": "status", "message": "Traduciendo..."}
{"type": "artifact", "download_url": "...", "expires_at": "..."}
```

**OBO for Teams**: the Bot Framework validates the user's Teams token, then performs the
on-behalf-of exchange for a token scoped to this agent's API. That exchanged token is what
arrives as the `Authorization: Bearer` header on `POST /invocations` - `identity.py`'s
`entra` mode validates its signature against Entra's JWKS and reads `tid`/`oid` from it. The
agent does not need to know or care that an OBO exchange happened upstream; it only needs a
validated user token.

## Copilot Studio

The best UX of the three. Copilot Studio has native support for long-running actions,
progress/status cards, and agent actions with adaptive cards, so the same event stream maps
onto:

- `StartWorkflow` (kicks off the invocation, returns immediately with an `operation_id`)
- `GetWorkflowStatus` (polled by Copilot Studio, backed by the same durable operation state
  in `storage/metadata_store.py` - since the workflow is checkpointed, this can be a cheap
  "what's the latest stage" read rather than holding an SSE connection open)
- `GetArtifact` (returns the SAS download link once `link_ready`)

**OBO for Copilot Studio**: Copilot Studio's connector/custom-connector layer authenticates
the end user and forwards a validated token (or performs its own OBO exchange) to the
backend action - the same `identity.py` `entra` path applies.

## M365 Copilot

Today, do not architect around arbitrary server-pushed progress events landing inside an
M365 Copilot chat turn - there isn't a general-purpose "server streams N intermediate
updates into this chat message" contract to depend on there. The reliable pattern is
start-then-poll-or-notify:

```text
User:      Translate this
Copilot:   Workflow started. Tracking ID: <operation_id>
  ...(elsewhere: a proactive notification, or the user asks "is it done?")
Copilot:   Artifact ready - download here: <link>
```

Because the workflow is idempotent and checkpointed, "is it done yet?" is just another call
to the same operation_id: if it already completed, `durable/engine.py`'s idempotent-replay
path returns immediately with a fresh download link instead of re-running anything.

**OBO for M365 Copilot**: identical shape - the Copilot orchestrator authenticates the user
and forwards (or OBO-exchanges for) a token this agent validates the same way.

## Why this doesn't change the agent's code

All three surfaces ultimately hand the hosted agent a validated bearer token for the signed-in
user and either read an SSE stream or poll a status. `identity.py` and
`durable/engine.py`/`hosted_agent/app.py` don't need per-channel branches - the channel-specific
work (OBO exchange, progress-card rendering, polling cadence) lives in each channel's own
connector/bot layer, upstream of this repo.
