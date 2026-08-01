"""OpenTelemetry setup: spans flow to App Insights and the Foundry Activity tab.

One correlation id per workflow run (§12 production control) — pass `run_tag`
into `span()` attributes and you can filter the whole fan-out in one query.
"""
from __future__ import annotations

import contextlib
import os
import uuid

from opentelemetry import trace

_tracer = None


def init(service_name: str) -> None:
    """Idempotent tracing init. Uses App Insights if configured, console otherwise."""
    global _tracer
    if _tracer is not None:
        return
    from .config import shared_env
    try:
        conn = shared_env().get("APPINSIGHTS_CONNECTION_STRING", "")
    except RuntimeError:
        conn = ""
    if conn:
        from azure.monitor.opentelemetry import configure_azure_monitor
        os.environ.setdefault("APPLICATIONINSIGHTS_CONNECTION_STRING", conn)
        configure_azure_monitor(connection_string=conn)
    _tracer = trace.get_tracer(service_name)


def new_run_tag(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@contextlib.contextmanager
def span(name: str, **attrs):
    tracer = _tracer or trace.get_tracer("reasoning-workshop")
    with tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            s.set_attribute(k, str(v))
        yield s
