"""W3C Trace Context (https://www.w3.org/TR/trace-context/) `traceparent`
propagation — docs/05-tier2-hosted-agents.md §6.3 / docs/06-tier3-durable-agents.md
§6.3, both flagged "the gap to close first": without a shared trace-id, one
slow or failing turn can't be followed from the chat client through the
gateway into the T2 Responses container span or the T3 orchestration/
activity spans.

No `opentelemetry-api` dependency. The `traceparent` header format is a
small, stable, publicly documented spec — not an Azure/OpenAI-specific
behavior this project's "verify against the actually-installed SDK"
discipline (docs/00 design premise #3) applies to. Actual span recording
and export is already handled by the platform's own auto-instrumentation
(docs/05 §6.3: "App Insights is injected and the protocol libraries emit
OpenTelemetry by default") — this gateway's own responsibility is
narrower and more concrete: read the inbound header correctly, and write a
correctly-formed outbound one on every hop it makes itself, so log lines
and downstream spans share one trace-id even where auto-instrumentation
can't reach across a process boundary on its own (T3's Durable Functions
activities specifically — see docs/06 §6.3's own note that this is harder
there, and `samples/tier3/01-durable-hello-world-status`'s README for how
this module's output gets relayed into an orchestration's `client_input`).
"""
from __future__ import annotations

import re
import secrets

_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-(?P<parent_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)
_INVALID_TRACE_ID = "0" * 32


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def parse(traceparent: str | None) -> str | None:
    """The trace-id from a `traceparent` header, or None if it's absent,
    malformed, or carries the spec's reserved all-zero trace-id (which the
    spec defines as invalid). Never raises: a bad or missing header means
    "this request starts a new trace here," not an error worth failing the
    request over."""
    if not traceparent:
        return None
    match = _TRACEPARENT_RE.match(traceparent.strip())
    if not match:
        return None
    trace_id = match.group("trace_id")
    if trace_id == _INVALID_TRACE_ID:
        return None
    return trace_id


def trace_id_for(inbound_traceparent: str | None) -> str:
    """The trace-id this request's whole call chain shares: the inbound
    one if it's a structurally valid W3C header, else a freshly minted
    one. This is the value logged and persisted (`gw_task.trace_id`);
    `outbound_header()` below is what actually goes on the wire for each
    hop."""
    return parse(inbound_traceparent) or new_trace_id()


def outbound_header(trace_id: str, *, sampled: bool = True) -> str:
    """A correctly-formed `traceparent` for an outbound call this gateway
    makes: same trace-id (the whole point — one id across every hop), a
    FRESH span-id, per the spec's own propagation model (the gateway's own
    hop is a new child span, not a re-announcement of whichever span sent
    the inbound request). Sampled by default: there's no local sampling
    decision to make without a real SDK/exporter wired into this process —
    see the module docstring for why that's the platform's job, not this
    function's."""
    flags = "01" if sampled else "00"
    return f"00-{trace_id}-{new_span_id()}-{flags}"
