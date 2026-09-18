"""OutcomeProvider plugin: optional verifier for AndroidWorld-style eval later.

Default is a no-op. Callables can wrap an env reward / success check. No LLM.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from mobilegui_ltm.diagnostics.schema import EpisodeTrace
from mobilegui_ltm.schema import AttemptOutcome, coerce_outcome


class OutcomeProvider(Protocol):
    """Optional post-episode verifier. Return None to keep ``trace.outcome``."""

    def evaluate(self, trace: EpisodeTrace) -> AttemptOutcome | None: ...


class NullOutcomeProvider:
    """Default: trust the episode's existing outcome."""

    def evaluate(self, trace: EpisodeTrace) -> AttemptOutcome | None:
        return trace.outcome


class CallableOutcomeProvider:
    """Wrap ``fn(trace) -> outcome`` for a later env verifier."""

    def __init__(self, fn: Callable[[EpisodeTrace], Any]) -> None:
        self.fn = fn

    def evaluate(self, trace: EpisodeTrace) -> AttemptOutcome | None:
        raw = self.fn(trace)
        if raw is None:
            return None
        return coerce_outcome(raw)
