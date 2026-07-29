"""Per-caller rate limiting for starting a new translation operation - the one call that costs
a real model invocation per request. Resuming an existing operation (reconnect/retry with the
same operation_id) is never rate limited - only work that would otherwise be repeated
indefinitely for free.

Downloads are not rate limited here: they go straight from the caller's browser to Blob
Storage via a SAS URL (see storage/blob_store.py's generate_download_url) - there is no
app-level endpoint in that path to attach a limiter to. Storage's own request-rate limits
apply instead; per-download auditing is handled by Azure Storage diagnostic logs
(docs/architecture.md's "Public storage + SAS" section).

This is a plain in-memory sliding window, scoped to one process - correct and sufficient for a
single hosted-agent instance. A multi-instance deployment would need the counters in a shared
store (Redis, or the same Table Storage backend already used for checkpoints/metadata) for the
limit to apply across replicas instead of per-replica; that swap is out of scope here, but
``enforce_invocation_rate_limit`` is the only call site that would need to change.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException

from .config import get_settings
from .models import CallerIdentity
from .observability import metrics


class RateLimitExceededError(Exception):
    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limit exceeded; retry after {retry_after_seconds:.1f}s")


class _SlidingWindowLimiter:
    """One deque of hit timestamps per key, pruned lazily on each check."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        if self.max_requests <= 0:
            return  # 0 (or negative) disables this limiter entirely
        now = time.monotonic()
        hits = self._hits[key]
        cutoff = now - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.max_requests:
            raise RateLimitExceededError(retry_after_seconds=hits[0] + self.window_seconds - now)
        hits.append(now)


def _caller_key(caller: CallerIdentity) -> str:
    return f"{caller.tenant_id}:{caller.user_object_id}"


_invocation_limiter: _SlidingWindowLimiter | None = None


def _get_invocation_limiter() -> _SlidingWindowLimiter:
    global _invocation_limiter
    if _invocation_limiter is None:
        settings = get_settings()
        _invocation_limiter = _SlidingWindowLimiter(
            max_requests=settings.lda_rate_limit_invocations_per_minute, window_seconds=60.0
        )
    return _invocation_limiter


def reset_rate_limiter_cache() -> None:
    """Test helper: forces the next enforce_invocation_rate_limit call to rebuild from current settings."""
    global _invocation_limiter
    _invocation_limiter = None


def enforce_invocation_rate_limit(caller: CallerIdentity) -> None:
    """Call only when a request is starting a genuinely new operation, never for a resume."""
    if not get_settings().lda_rate_limit_enabled:
        return
    try:
        _get_invocation_limiter().check(_caller_key(caller))
    except RateLimitExceededError as exc:
        metrics()["invocation_rate_limited_total"].inc()
        retry_after = max(1, int(exc.retry_after_seconds) + 1)
        raise HTTPException(
            status_code=429,
            detail="Too many new translation requests - please slow down and try again shortly.",
            headers={"Retry-After": str(retry_after)},
        ) from exc
