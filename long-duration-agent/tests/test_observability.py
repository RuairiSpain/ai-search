"""Regression coverage for observability.py's graceful degradation when prometheus_client
isn't installed - metrics() already degraded to no-ops in that case, but the GET /metrics
endpoint itself (metrics_endpoint_response) didn't: it unconditionally imported
prometheus_client, so scraping /metrics on a base [dev] install (LDA_METRICS_ENABLED defaults
to true, but the observability extra doesn't) raised an unhandled ModuleNotFoundError."""

import importlib.util
import sys
from unittest import mock

import pytest

from long_duration_agent.observability import metrics_endpoint_response

PROMETHEUS_CLIENT_INSTALLED = importlib.util.find_spec("prometheus_client") is not None
requires_prometheus_client = pytest.mark.skipif(
    not PROMETHEUS_CLIENT_INSTALLED, reason="prometheus-client not installed (pip install '.[observability]')"
)


def test_metrics_endpoint_degrades_gracefully_without_prometheus_client():
    with mock.patch.dict(sys.modules, {"prometheus_client": None}):
        content, content_type = metrics_endpoint_response()

    assert content == b""
    assert content_type == "text/plain; charset=utf-8"


@requires_prometheus_client
def test_metrics_endpoint_returns_real_prometheus_output_when_installed():
    content, content_type = metrics_endpoint_response()

    assert isinstance(content, bytes)
    assert content_type.startswith("text/plain; version=")
