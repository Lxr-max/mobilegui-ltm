"""Lightweight failure taxonomy from outcome.reason / trajectory text (no LLM)."""

from __future__ import annotations

import re

from mobilegui_ltm.diagnostics.schema import EpisodeTrace, FailureClass
from mobilegui_ltm.encode.text import traj_text
from mobilegui_ltm.schema import AttemptOutcome, OutcomeStatus, Trajectory, coerce_outcome, coerce_trajectory

_PATTERNS: tuple[tuple[FailureClass, tuple[str, ...]], ...] = (
    (
        FailureClass.STATE_LOSS,
        (
            r"\bstate loss\b",
            r"\blost state\b",
            r"\bsession expired\b",
            r"\blogged out\b",
            r"\bapp (?:killed|crashed|restarted)\b",
            r"\bactivity destroyed\b",
            r"\bemulator reset\b",
            r"\bprocess died\b",
        ),
    ),
    (
        FailureClass.INTERRUPTION,
        (
            r"\binterrupt",
            r"\btimeout\b",
            r"\btimed out\b",
            r"\bcancel(?:led|ed)?\b",
            r"\bpermission (?:denied|dialog)\b",
            r"\bsystem dialog\b",
            r"\banr\b",
        ),
    ),
    (
        FailureClass.CONTEXT_DRIFT,
        (
            r"\bcontext drift\b",
            r"\bstale\b",
            r"\boutdated\b",
            r"\bwrong screen\b",
            r"\bwrong activity\b",
            r"\bforgot\b",
            r"\boff[- ]task\b",
            r"\bdrift\b",
        ),
    ),
    (
        FailureClass.UNVERIFIED_PROGRESS,
        (
            r"\bunverified\b",
            r"\bnot confirmed\b",
            r"\bclaimed success\b",
            r"\bno verifier\b",
            r"\bprogress not\b",
            r"\bpartial(?:ly)? complete\b",
        ),
    ),
    (
        FailureClass.MISBINDING,
        (
            r"\bmisbind",
            r"\bwrong (?:seller|item|product|widget|button|tab|app)\b",
            r"\bunavailable at\b",
            r"\bincorrect (?:target|widget|control)\b",
            r"\btapped the wrong\b",
            r"\bselected the wrong\b",
            r"\bmismatch\b",
            r"\bdoes not (?:sell|carry|have)\b",
        ),
    ),
)


def classify_failure(
    outcome: AttemptOutcome | str | bool | dict | None = None,
    traj: Trajectory | dict | list | str | None = None,
    *,
    trace: EpisodeTrace | None = None,
) -> FailureClass:
    """Return a FailureClass from reason + trajectory text.

    Success / partial episodes default to ``unknown`` unless the reason
    itself matches a class (unusual).
    """
    if trace is not None:
        outcome = trace.outcome
        traj = trace.traj
    result = coerce_outcome(outcome)
    trajectory = coerce_trajectory(traj)
    if result.status is not OutcomeStatus.FAILURE:
        return FailureClass.UNKNOWN
    blob = f"{result.reason or ''} {traj_text(trajectory)}".lower()
    if not blob.strip():
        return FailureClass.UNKNOWN
    for bucket, patterns in _PATTERNS:
        for pattern in patterns:
            if re.search(pattern, blob, flags=re.IGNORECASE):
                return bucket
    return FailureClass.UNKNOWN
