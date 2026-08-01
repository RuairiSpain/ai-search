"""Thread-safe, content-aware caching for expensive registration calls
(agents, vector stores, service clients) that several patterns build once per
process and reuse.

Two failure modes this fixes (project review, item 12):
  1. Not thread-safe: a plain `dict` read-then-written from multiple call
     sites (or, in a future parallelized pattern, multiple threads) can race.
  2. Stale after an edit: a cache keyed by NAME alone means editing
     instructions.md and re-running in the same process returns the OLD
     agent id — the edit never takes effect until the process restarts.

ContentCache fixes both: a lock guards every read/write, and the cache key
includes a hash of the content that determines the cached value, so an
edited instructions file (or tool list, or anything else folded into the
hashed content) invalidates automatically instead of silently serving stale
state.
"""
from __future__ import annotations

import hashlib
import threading
from typing import Callable, TypeVar

T = TypeVar("T")


class ContentCache:
    """get_or_create(key, content, factory): re-runs factory() only when the
    key is new OR the content hash has changed since the last call for that
    key. factory() runs OUTSIDE the lock (it's typically a network call —
    agent upsert, vector store creation — and holding a lock across a
    network round-trip would serialize otherwise-independent registrations
    and risks a deadlock if factory() ever re-enters the cache).

    Guarantee: the store itself never corrupts under concurrent access, and
    every reader eventually sees a value consistent with SOME successful
    factory() call for the current content hash.

    NOT guaranteed: if multiple threads race a cold cache for the SAME key
    at the same time, more than one may call factory() before either write
    lands (a "thundering herd", not a "race condition" in the corrupting
    sense). This is fine here because every factory in this codebase is an
    idempotent upsert/lookup-or-create against the service — redundant calls
    converge on the same end state, just with wasted round-trips. True
    dedup would need a per-key lock held across the network call, trading
    that waste for serializing genuinely independent registrations instead.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, tuple[str, object]] = {}

    @staticmethod
    def hash_of(*parts: str) -> str:
        h = hashlib.sha256()
        for p in parts:
            h.update(p.encode("utf-8"))
            h.update(b"\0")
        return h.hexdigest()[:16]

    def get_or_create(self, key: str, content: str, factory: Callable[[], T]) -> T:
        digest = self.hash_of(content)
        with self._lock:
            cached = self._store.get(key)
            if cached is not None and cached[0] == digest:
                return cached[1]  # type: ignore[return-value]
        value = factory()
        with self._lock:
            self._store[key] = (digest, value)
        return value

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def snapshot(self) -> dict:
        """For tests/inspection: {key: content_hash}."""
        with self._lock:
            return {k: v[0] for k, v in self._store.items()}


class SingletonCache:
    """Simpler cache for a single lazily-built object with no content key
    (e.g. a service client). Thread-safe get-or-build; no staleness concept
    since there's no editable content driving it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: object | None = None
        self._built = False

    def get_or_create(self, factory: Callable[[], T]) -> T:
        with self._lock:
            if self._built:
                return self._value  # type: ignore[return-value]
        value = factory()
        with self._lock:
            if not self._built:  # another thread may have won the race
                self._value = value
                self._built = True
            return self._value  # type: ignore[return-value]
