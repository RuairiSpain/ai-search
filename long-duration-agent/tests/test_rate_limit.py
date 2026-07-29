import time

import pytest
from fastapi import HTTPException

from long_duration_agent.config import get_settings
from long_duration_agent.models import CallerIdentity
from long_duration_agent.rate_limit import _SlidingWindowLimiter, enforce_invocation_rate_limit, reset_rate_limiter_cache

CALLER = CallerIdentity(tenant_id="tenant-a", user_object_id="user-1")
OTHER_CALLER = CallerIdentity(tenant_id="tenant-b", user_object_id="user-2")


def _configure(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_rate_limiter_cache()


def test_invocation_limit_allows_up_to_the_configured_count_then_rejects(monkeypatch):
    _configure(monkeypatch, LDA_RATE_LIMIT_INVOCATIONS_PER_MINUTE="3")

    for _ in range(3):
        enforce_invocation_rate_limit(CALLER)

    with pytest.raises(HTTPException) as exc_info:
        enforce_invocation_rate_limit(CALLER)
    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers


def test_invocation_limit_is_scoped_per_caller(monkeypatch):
    _configure(monkeypatch, LDA_RATE_LIMIT_INVOCATIONS_PER_MINUTE="1")

    enforce_invocation_rate_limit(CALLER)
    with pytest.raises(HTTPException):
        enforce_invocation_rate_limit(CALLER)

    # A different caller has its own, untouched bucket.
    enforce_invocation_rate_limit(OTHER_CALLER)


def test_zero_disables_a_limiter(monkeypatch):
    _configure(monkeypatch, LDA_RATE_LIMIT_INVOCATIONS_PER_MINUTE="0")

    for _ in range(50):
        enforce_invocation_rate_limit(CALLER)  # never raises


def test_rate_limit_enabled_false_disables_the_limiter(monkeypatch):
    _configure(monkeypatch, LDA_RATE_LIMIT_ENABLED="false", LDA_RATE_LIMIT_INVOCATIONS_PER_MINUTE="1")

    for _ in range(10):
        enforce_invocation_rate_limit(CALLER)


def test_sweep_evicts_keys_with_nothing_left_in_the_window():
    """Regression test: a caller checked once and never seen again must not occupy a dict
    entry forever - otherwise a long-running process accumulates one entry per distinct
    caller ever seen, unbounded."""
    limiter = _SlidingWindowLimiter(max_requests=5, window_seconds=0.05)
    limiter._SWEEP_INTERVAL_SECONDS = 0  # force every check() to attempt a sweep

    limiter.check("one-time-caller")
    assert "one-time-caller" in limiter._hits

    time.sleep(0.1)  # past the window, so "one-time-caller"'s hit is now stale
    limiter.check("a-different-caller")  # triggers the sweep as a side effect

    assert "one-time-caller" not in limiter._hits
    assert "a-different-caller" in limiter._hits


def test_reset_rate_limiter_cache_clears_accumulated_state(monkeypatch):
    _configure(monkeypatch, LDA_RATE_LIMIT_INVOCATIONS_PER_MINUTE="1")

    enforce_invocation_rate_limit(CALLER)
    with pytest.raises(HTTPException):
        enforce_invocation_rate_limit(CALLER)

    reset_rate_limiter_cache()
    enforce_invocation_rate_limit(CALLER)  # fresh bucket, does not raise


@pytest.mark.asyncio
async def test_new_operations_are_rate_limited_but_resumes_are_not(monkeypatch):
    """Exercises the actual /invocations endpoint wiring, not just the limiter in isolation:
    a resumed operation_id must never be blocked by the new-operation limit, no matter how
    exhausted that limit is."""
    monkeypatch.setenv("LDA_RATE_LIMIT_INVOCATIONS_PER_MINUTE", "1")
    get_settings.cache_clear()
    reset_rate_limiter_cache()

    from long_duration_agent.hosted_agent.app import invoke
    from long_duration_agent.models import InvocationRequest
    from long_duration_agent.storage.metadata_store import get_metadata_store

    store = get_metadata_store()
    await store.start_operation(
        operation_id="existing-op", workflow_name="wf", tenant_id=CALLER.tenant_id, user_object_id=CALLER.user_object_id
    )
    await store.complete_operation("existing-op", artifact_id="existing-op")

    # Exhaust the new-operation limit.
    await invoke(InvocationRequest(prompt="first"), caller=CALLER)
    with pytest.raises(HTTPException) as exc_info:
        await invoke(InvocationRequest(prompt="second"), caller=CALLER)
    assert exc_info.value.status_code == 429

    # A resume of an existing (already-completed) operation still goes through - it re-runs
    # the idempotent-replay path in run_translation_operation rather than being rejected here.
    response = await invoke(InvocationRequest(prompt="first", operation_id="existing-op"), caller=CALLER)
    assert response is not None
