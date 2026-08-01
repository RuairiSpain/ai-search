"""Bridges a2a-sdk's `ServerCallContext` to our own principal validation.

`ServerCallContextBuilder.build(request)` runs once per inbound HTTP
request, before any A2A dispatch — this is where Entra validation lives
now, replacing the hand-rolled `_principal()` helper that used to live in
api/a2a.py. Raising `starlette.exceptions.HTTPException` here is caught
and re-raised as-is by the SDK's dispatchers (verified against the
installed a2a-sdk source: `jsonrpc_dispatcher.py`'s outer `except
HTTPException as e: ... raise e`), so a 401 comes back clean, never
wrapped in a JSON-RPC error envelope.

Also where trace correlation starts (docs/05 §6.3, docs/06 §6.3, "the gap
to close first"): every inbound request's raw headers land in
`state["headers"]` below regardless, so extracting `traceparent` here —
rather than adding a separate middleware — costs nothing extra and keeps
every piece of per-request state entering the system through the one
place this module's own docstring already calls out as the entry point.
"""
from __future__ import annotations

from a2a.server.context import ServerCallContext
from a2a.server.routes.common import ServerCallContextBuilder
from starlette.exceptions import HTTPException
from starlette.requests import Request

from gateway.auth.principal import AuthError, EntraValidator, Principal
from gateway.tracing import trace_id_for

_PRINCIPAL_KEY = "principal"
_TRACE_ID_KEY = "trace_id"


class GatewayCallContextBuilder(ServerCallContextBuilder):
    """This is the ONLY place a user identity enters the system — same
    invariant as EntraValidator's own docstring states. Nothing downstream
    may read a principal from anywhere but `call_context.state`."""

    def __init__(self, validator: EntraValidator):
        self._validator = validator

    def build(self, request: Request) -> ServerCallContext:
        authorization = request.headers.get("authorization")
        try:
            principal = self._validator.principal_from(authorization)
        except AuthError as exc:
            raise HTTPException(status_code=401) from exc
        # `headers` must be present for the SDK's own machinery to work --
        # e.g. `validate_version` reads context.state["headers"] to check
        # A2A-Version, and defaults to rejecting the request as version
        # 0.3 if it's missing entirely. Mirrors DefaultServerCallContextBuilder.
        headers = dict(request.headers)
        trace_id = trace_id_for(headers.get("traceparent"))
        return ServerCallContext(
            state={_PRINCIPAL_KEY: principal, _TRACE_ID_KEY: trace_id, "headers": headers}
        )


def principal_from(call_context: ServerCallContext) -> Principal:
    """Single source of truth for the state key name — every call site
    reads the principal through this, never `call_context.state[...]`
    directly."""
    principal = call_context.state.get(_PRINCIPAL_KEY)
    if principal is None:
        # Unreachable if GatewayCallContextBuilder is wired correctly;
        # fail loudly rather than silently treat as unauthenticated.
        raise AuthError("no principal on this call context")
    return principal


def trace_id_from(call_context: ServerCallContext) -> str:
    """Same single-source-of-truth reasoning as principal_from() above.
    Unlike a missing principal, a missing trace_id is not a hard failure
    mode worth raising over — mint one on the spot, so a call context built
    by anything other than GatewayCallContextBuilder (a test double, say)
    degrades to "starts its own trace" rather than crashing."""
    trace_id = call_context.state.get(_TRACE_ID_KEY)
    if trace_id is None:
        return trace_id_for(None)
    return trace_id
