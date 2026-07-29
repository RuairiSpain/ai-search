"""Tracing, metrics, and correlated structured logging for the whole operation lifecycle.

Tracing: ``agent_framework`` already wraps every ``Workflow.run()`` in its own OpenTelemetry
span (``workflow.run``, with ``workflow.started``/``workflow.completed`` events) - confirmed
by calling ``configure_otel_providers`` and running a real workflow, which produced exactly
that span with no extra instrumentation code needed anywhere in ``pipeline.py``.
``configure_observability()`` below just wires up where those spans (and ours - see
``tracer``) go: nowhere (default), the console, or an OTLP collector.

Metrics: a small set of ``prometheus_client`` counters/histograms tracking what actually
matters for a long-running-agent workload - operations started/completed/failed/stopped,
end-to-end duration, translation call duration, steering messages processed, and how many
operations are currently paused waiting on a human. Scraped via ``/metrics`` on the hosted-agent
app (see ``metrics_endpoint``). Downloads themselves aren't tracked here - they go straight to
Blob Storage via a SAS URL, never through this app; see Azure Storage's own diagnostic logs for
that (docs/architecture.md's "Public storage + SAS" section).

Logging: ``operation_log_context`` binds an operation_id to a contextvar for the duration of
a request; ``JsonLogFormatter`` includes it in every log line emitted while that context is
active, so every log line for one operation - across validate/translate/upload/steering/HITL -
can be correlated by grepping one id, including across a resume that happens in a later,
unrelated request.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
from contextlib import contextmanager
from typing import Iterator

from .config import get_settings

_configured = False
_operation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("lda_operation_id", default=None)


def configure_observability() -> None:
    """Idempotent - call once at process startup (each FastAPI app's module does this)."""
    global _configured
    if _configured:
        return
    _configured = True

    settings = get_settings()
    if settings.lda_otel_exporter == "none":
        return

    from agent_framework.observability import configure_otel_providers

    if settings.lda_otel_exporter == "console":
        configure_otel_providers(enable_console_exporters=True)
    elif settings.lda_otel_exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = settings.lda_otel_endpoint or None
        configure_otel_providers(
            exporters=[OTLPSpanExporter(endpoint=endpoint), OTLPMetricExporter(endpoint=endpoint)]
        )


def get_tracer():
    from opentelemetry import trace

    return trace.get_tracer(get_settings().lda_service_name)


@contextmanager
def operation_log_context(operation_id: str) -> Iterator[None]:
    """All logging.getLogger(...).info(...) calls made while this is active get operation_id
    attached, via JsonLogFormatter - correlate one operation's log lines across every step."""
    token = _operation_id_var.set(operation_id)
    try:
        yield
    finally:
        _operation_id_var.reset(token)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        operation_id = _operation_id_var.get()
        if operation_id is not None:
            payload["operation_id"] = operation_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_json_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


# ---- metrics ------------------------------------------------------------------------

class _NoopMetric:
    """Used when prometheus_client isn't installed or metrics are disabled - every call
    becomes a no-op instead of every call site needing an `if metrics_enabled` check."""

    def labels(self, *_args, **_kwargs) -> "_NoopMetric":
        return self

    def inc(self, *_args, **_kwargs) -> None:
        pass

    def dec(self, *_args, **_kwargs) -> None:
        pass

    def observe(self, *_args, **_kwargs) -> None:
        pass

    def set(self, *_args, **_kwargs) -> None:
        pass


def _make_metrics():
    settings = get_settings()
    if not settings.lda_metrics_enabled:
        noop = _NoopMetric()
        return {name: noop for name in _METRIC_NAMES}

    try:
        from prometheus_client import Counter, Gauge, Histogram
    except ModuleNotFoundError:
        noop = _NoopMetric()
        return {name: noop for name in _METRIC_NAMES}

    return {
        "operations_started": Counter(
            "lda_operations_started_total", "Translation operations started"
        ),
        "operations_completed": Counter(
            "lda_operations_completed_total", "Translation operations completed successfully"
        ),
        "operations_failed": Counter("lda_operations_failed_total", "Translation operations that raised"),
        "operations_stopped": Counter(
            "lda_operations_stopped_total", "Translation operations stopped via HITL"
        ),
        "operation_duration_seconds": Histogram(
            "lda_operation_duration_seconds", "End-to-end duration of a completed operation"
        ),
        "translation_duration_seconds": Histogram(
            "lda_translation_duration_seconds", "Duration of a single translate_to_spanish() call"
        ),
        "steering_messages_total": Counter(
            "lda_steering_messages_total", "Steering messages queued via /steer"
        ),
        "waiting_hitl_gauge": Gauge(
            "lda_operations_waiting_hitl", "Operations currently paused waiting on a HITL response"
        ),
        "invocation_rate_limited_total": Counter(
            "lda_invocation_rate_limited_total", "New-operation requests rejected by the rate limiter"
        ),
        "content_safety_blocked_total": Counter(
            "lda_content_safety_blocked_total", "Prompts rejected by the content safety guardrail"
        ),
    }


_METRIC_NAMES = [
    "operations_started",
    "operations_completed",
    "operations_failed",
    "operations_stopped",
    "operation_duration_seconds",
    "translation_duration_seconds",
    "steering_messages_total",
    "waiting_hitl_gauge",
    "invocation_rate_limited_total",
    "content_safety_blocked_total",
]

_metrics: dict | None = None


def metrics() -> dict:
    global _metrics
    if _metrics is None:
        _metrics = _make_metrics()
    return _metrics


def reset_metrics_cache() -> None:
    """Test helper: forces re-creation (prometheus_client raises on duplicate metric names,
    which only matters across repeated test-process metric registration, not production)."""
    global _metrics
    _metrics = None


def metrics_endpoint_response():
    """Returns (content_bytes, content_type) for a GET /metrics handler."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return generate_latest(), CONTENT_TYPE_LATEST


@contextmanager
def timer() -> Iterator[Iterator[float]]:
    """with timer() as elapsed: ... ; elapsed() -> seconds so far / at exit."""
    start = time.perf_counter()
    yield lambda: time.perf_counter() - start
