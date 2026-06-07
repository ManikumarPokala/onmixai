"""Per-model circuit breaker for the LLM gateway (patterns.md §10).

In-process state with an explicit lifecycle and an injected clock — no module-level
mutable globals, so it is deterministically testable and DI-provided per gateway.
A model that fails ``failure_threshold`` times consecutively is OPEN (skipped without
an attempt) for ``reset_seconds``, then HALF-OPEN: a single probe is allowed; its
outcome closes the breaker (success) or re-opens it (failure).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"  # healthy — attempts allowed
    OPEN = "open"  # failing — attempts skipped until the reset window elapses
    HALF_OPEN = "half_open"  # reset elapsed — a single probe is in flight


@dataclass
class _Entry:
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    opened_at: float = 0.0


@dataclass
class CircuitBreaker:
    failure_threshold: int
    reset_seconds: float
    clock: Callable[[], float] = time.monotonic
    _entries: dict[str, _Entry] = field(default_factory=dict)

    def state(self, key: str) -> CircuitState:
        return self._entries.get(key, _Entry()).state

    def allow(self, key: str) -> bool:
        """Whether an attempt against ``key`` may proceed now.

        OPEN→HALF_OPEN transitions here once the reset window elapses, returning the
        single probe. Time/Space: O(1).
        """
        entry = self._entries.get(key)
        if entry is None or entry.state == CircuitState.CLOSED:
            return True
        if entry.state == CircuitState.OPEN:
            if self.clock() - entry.opened_at >= self.reset_seconds:
                entry.state = CircuitState.HALF_OPEN  # release one probe
                return True
            return False
        return False  # HALF_OPEN: a probe is already out — no concurrent probes

    def record_success(self, key: str) -> None:
        """A successful attempt closes the breaker. Time/Space: O(1)."""
        self._entries[key] = _Entry()

    def record_failure(self, key: str) -> None:
        """A failed attempt; opens the breaker at the threshold (or immediately if a
        HALF_OPEN probe failed). Time/Space: O(1)."""
        entry = self._entries.setdefault(key, _Entry())
        entry.failures += 1
        if entry.state == CircuitState.HALF_OPEN or entry.failures >= self.failure_threshold:
            entry.state = CircuitState.OPEN
            entry.opened_at = self.clock()
