"""Runtime diagnostic records: traces, attributions, proposed memory actions.

Failure classes follow a lightweight mobile-GUI taxonomy (state loss, widget
misbinding, context drift, unverified progress, interruption). No LLM.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from mobilegui_ltm.schema import (
    AttemptOutcome,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    Trajectory,
)


class FailureClass(str, Enum):
    """Heuristic failure bucket for one episode (no LLM)."""

    STATE_LOSS = "state_loss"
    MISBINDING = "misbinding"
    CONTEXT_DRIFT = "context_drift"
    UNVERIFIED_PROGRESS = "unverified_progress"
    INTERRUPTION = "interruption"
    UNKNOWN = "unknown"


class Attribution(str, Enum):
    """Whether a retrieved memory appears to have helped, hurt, or gone unused."""

    HELPED = "helped"
    HURT = "hurt"
    UNUSED = "unused"


class MemoryActionType(str, Enum):
    PROMOTE = "promote"
    DEMOTE = "demote"
    QUARANTINE = "quarantine"
    UNQUARANTINE = "unquarantine"
    REWRITE_TIP = "rewrite_tip"
    REWRITE_SHORTCUT = "rewrite_shortcut"


# Conservative runtime actions (human-in-the-loop for reflector rewrites).
AUTO_ACTION_TYPES = frozenset(
    {
        MemoryActionType.PROMOTE,
        MemoryActionType.DEMOTE,
        MemoryActionType.QUARANTINE,
        MemoryActionType.UNQUARANTINE,
    }
)
REFLECTOR_ACTION_TYPES = frozenset(
    {MemoryActionType.REWRITE_TIP, MemoryActionType.REWRITE_SHORTCUT}
)

QUARANTINE_META = "quarantined"
QUARANTINE_REASON_META = "quarantine_reason"
QUARANTINE_AT_META = "quarantined_at"


class MemoryAction(BaseModel):
    """A proposed (or applied) store mutation."""

    type: MemoryActionType
    target_id: str | None = None
    logical_key: str | None = None
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    auto: bool = True

    def target(self) -> str | None:
        return self.target_id or self.logical_key


class DiagnosticEvent(BaseModel):
    """One attribution, taxonomy, or audit note."""

    kind: str
    message: str = ""
    record_id: str | None = None
    logical_key: str | None = None
    memory_kind: MemoryKind | None = None
    attribution: Attribution | None = None
    failure_class: FailureClass | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EpisodeTrace(BaseModel):
    """What happened on one attempt, enough to score memory usefulness."""

    task_id: str
    attempt_k: int
    agent_id: str = "default"
    query: str = ""
    traj: Trajectory = Field(default_factory=Trajectory)
    outcome: AttemptOutcome = Field(default_factory=AttemptOutcome)
    memories_retrieved: list[MemoryRecord] = Field(default_factory=list)
    memories_written: list[MemoryRecord] = Field(default_factory=list)
    injected: bool = False
    previous_outcome: AttemptOutcome | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.outcome.status is OutcomeStatus.SUCCESS

    @property
    def failed(self) -> bool:
        return self.outcome.status is OutcomeStatus.FAILURE

    @property
    def recovery_delta(self) -> bool:
        prev = self.previous_outcome
        return (
            prev is not None
            and prev.status is OutcomeStatus.FAILURE
            and self.outcome.status is OutcomeStatus.SUCCESS
        )


class DiagnosticReport(BaseModel):
    """Attribution, failure class, and conservative action proposals for one episode."""

    task_id: str
    attempt_k: int
    outcome: AttemptOutcome
    failure_class: FailureClass = FailureClass.UNKNOWN
    attributions: list[DiagnosticEvent] = Field(default_factory=list)
    actions: list[MemoryAction] = Field(default_factory=list)
    reflector_actions: list[MemoryAction] = Field(default_factory=list)
    events: list[DiagnosticEvent] = Field(default_factory=list)
    audit_findings: list[DiagnosticEvent] = Field(default_factory=list)
    recovery_delta: bool = False
    dry_run: bool = True
    applied: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    def attribution_counts(self) -> dict[str, int]:
        counts = {item.value: 0 for item in Attribution}
        for event in self.attributions:
            if event.attribution is not None:
                counts[event.attribution.value] += 1
        return counts

    def actions_of(self, *types: MemoryActionType | str) -> list[MemoryAction]:
        wanted = {
            item if isinstance(item, MemoryActionType) else MemoryActionType(item)
            for item in types
        }
        return [action for action in self.actions if action.type in wanted]

    def format_summary(self) -> str:
        status = self.outcome.status.value.upper()
        counts = self.attribution_counts()
        return (
            f"attempt {self.attempt_k}  {status}  class={self.failure_class.value}  "
            f"helped={counts['helped']} hurt={counts['hurt']} unused={counts['unused']}  "
            f"recovery_delta={int(self.recovery_delta)}"
        )


def is_quarantined(record: MemoryRecord) -> bool:
    return bool((record.metadata or {}).get(QUARANTINE_META))
