"""Structured memory schema for mobile GUI long-term memory.

Memory kinds are GUI-specific (UI facts, subgoal traces, failure notes,
and phase-2 shortcuts). This is not a generic chat-memory log.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


SCHEMA_VERSION = "1"


class MemoryKind(str, Enum):
    """Kinds of records the SDK persists."""

    UI_FACT = "ui_fact"
    SUBGOAL_TRACE = "subgoal_trace"
    FAILURE_NOTE = "failure_note"
    SHORTCUT = "shortcut"  # phase 2 — encoder stub only in MVP


class OutcomeStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


class InjectionTarget(str, Enum):
    """Which agent segment receives retrieved memory."""

    SYSTEM = "system"
    PLANNER = "planner"
    WORKER = "worker"


class TrajectoryStep(BaseModel):
    """One observation/action pair from a mobile GUI episode."""

    observation: str | dict[str, Any] | None = None
    action: str | dict[str, Any] | None = None
    app_id: str | None = None
    screen: str | None = None
    timestamp: datetime | None = None
    extras: dict[str, Any] = Field(default_factory=dict)


class Trajectory(BaseModel):
    """A single attempt's GUI trajectory.

    Extra structured hints may be placed in ``metadata``:

    - ``ui_facts``: list[str]
    - ``subgoals``: list[str]
    - ``failure_notes``: list[str]
    - ``apps`` / ``app_ids``: list[str]
    """

    steps: list[TrajectoryStep] = Field(default_factory=list)
    app_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AttemptOutcome(BaseModel):
    """Success or failure of one attempt (always written, including failures)."""

    status: OutcomeStatus = OutcomeStatus.FAILURE
    reason: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRecord(BaseModel):
    """One persisted memory item."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    kind: MemoryKind
    content: str
    task_id: str
    attempt_k: int
    agent_id: str
    app_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] | None = None

    def searchable_text(self) -> str:
        apps = " ".join(self.app_ids)
        tags = " ".join(self.tags)
        return " ".join(
            part
            for part in (
                self.kind.value,
                self.content,
                self.task_id,
                apps,
                tags,
            )
            if part
        )


class SessionBundle(BaseModel):
    """Portable dump for cross-machine reproduction."""

    schema_version: str = SCHEMA_VERSION
    agent_id: str
    exported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    memories: list[MemoryRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalTask(BaseModel):
    """Minimal task descriptor used by the pass@k adapter and demo."""

    task_id: str
    instruction: str
    query: str | None = None
    app_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def retrieval_query(self) -> str:
        return self.query or self.instruction


def coerce_trajectory(traj: Trajectory | dict[str, Any] | list[Any] | str | None) -> Trajectory:
    """Accept a Trajectory, dict, list of steps, or free text."""
    if traj is None:
        return Trajectory()
    if isinstance(traj, Trajectory):
        return traj
    if isinstance(traj, str):
        return Trajectory(steps=[TrajectoryStep(observation=traj)])
    if isinstance(traj, list):
        steps: list[TrajectoryStep] = []
        app_ids: list[str] = []
        for item in traj:
            if isinstance(item, TrajectoryStep):
                steps.append(item)
                if item.app_id:
                    app_ids.append(item.app_id)
            elif isinstance(item, dict):
                step = _step_from_dict(item)
                steps.append(step)
                if step.app_id:
                    app_ids.append(step.app_id)
            else:
                steps.append(TrajectoryStep(observation=str(item)))
        return Trajectory(steps=steps, app_ids=_unique(app_ids))
    if isinstance(traj, dict):
        if "steps" in traj or "app_ids" in traj or "metadata" in traj:
            parsed = Trajectory.model_validate(traj)
            if not parsed.app_ids:
                parsed.app_ids = _unique(
                    [s.app_id for s in parsed.steps if s.app_id]
                    + list(parsed.metadata.get("app_ids") or parsed.metadata.get("apps") or [])
                )
            return parsed
        return Trajectory(steps=[_step_from_dict(traj)], app_ids=_unique([traj.get("app_id")]))
    raise TypeError(f"Unsupported trajectory type: {type(traj)!r}")


def coerce_outcome(outcome: AttemptOutcome | OutcomeStatus | dict[str, Any] | str | bool | None) -> AttemptOutcome:
    """Accept AttemptOutcome, status string, bool, or dict."""
    if outcome is None:
        return AttemptOutcome(status=OutcomeStatus.FAILURE)
    if isinstance(outcome, AttemptOutcome):
        return outcome
    if isinstance(outcome, OutcomeStatus):
        return AttemptOutcome(status=outcome)
    if isinstance(outcome, bool):
        return AttemptOutcome(
            status=OutcomeStatus.SUCCESS if outcome else OutcomeStatus.FAILURE
        )
    if isinstance(outcome, str):
        lowered = outcome.strip().lower()
        if lowered in {s.value for s in OutcomeStatus}:
            return AttemptOutcome(status=OutcomeStatus(lowered))
        return AttemptOutcome(status=OutcomeStatus.FAILURE, reason=outcome)
    if isinstance(outcome, dict):
        if "status" not in outcome and "success" in outcome:
            outcome = dict(outcome)
            outcome["status"] = (
                OutcomeStatus.SUCCESS if outcome.pop("success") else OutcomeStatus.FAILURE
            )
        return AttemptOutcome.model_validate(outcome)
    raise TypeError(f"Unsupported outcome type: {type(outcome)!r}")


def _step_from_dict(item: dict[str, Any]) -> TrajectoryStep:
    extras = {
        k: v
        for k, v in item.items()
        if k
        not in {
            "observation",
            "obs",
            "action",
            "app_id",
            "app",
            "screen",
            "activity",
            "timestamp",
            "extras",
        }
    }
    return TrajectoryStep(
        observation=item.get("observation", item.get("obs")),
        action=item.get("action"),
        app_id=item.get("app_id") or item.get("app"),
        screen=item.get("screen") or item.get("activity"),
        timestamp=item.get("timestamp"),
        extras={**(item.get("extras") or {}), **extras},
    )


def _unique(values: list[str | None]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen
