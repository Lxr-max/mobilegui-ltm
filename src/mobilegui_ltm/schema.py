"""Structured memory schema for mobile GUI long-term memory.

Memory kinds are GUI-specific (UI facts, subgoal traces, failure notes,
shortcuts, causal anchors). This is not a generic chat-memory log.
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
    SHORTCUT = "shortcut"
    CAUSAL_ANCHOR = "causal_anchor"
    APP_PRIOR = "app_prior"


PROMOTABLE_KINDS = frozenset({MemoryKind.SHORTCUT, MemoryKind.CAUSAL_ANCHOR})


class RecordStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class Stability(str, Enum):
    """Promotion gate for shortcuts / anchors (observations stay stable)."""

    CANDIDATE = "candidate"
    STABLE = "stable"
    RETIRED = "retired"


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

    - ``ui_facts``: list[str] | list[{key, content, screen}]
    - ``subgoals``: list[str]
    - ``failure_notes``: list[str]
    - ``shortcuts``: list[str] | list[dict] (executable snippets)
    - ``preconditions``: list[str]
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


class Evidence(BaseModel):
    """Where a causal anchor was observed (no graph DB — just pointers)."""

    app_id: str | None = None
    screen: str | None = None
    widget: str | None = None


class AtomicAction(BaseModel):
    """One executable GUI step inside a shortcut."""

    type: str
    target: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    app_id: str | None = None
    screen: str | None = None
    description: str | None = None

    def as_text(self) -> str:
        extra = ",".join(f"{k}={v}" for k, v in self.args.items()) if self.args else ""
        if self.target and extra and extra not in self.target:
            return f"{self.type}:{self.target}({extra})"
        if self.target:
            return f"{self.type}:{self.target}"
        if extra:
            return f"{self.type}:{extra}"
        return self.type


class ShortcutSpec(BaseModel):
    """Executable shortcut (skill-style, SDK-side only)."""

    name: str
    description: str = ""
    preconditions: list[str] = Field(default_factory=list)
    arguments: dict[str, Any] = Field(default_factory=dict)
    atomic_actions: list[AtomicAction] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    app_ids: list[str] = Field(default_factory=list)
    subgoal: str | None = None

    def action_texts(self) -> list[str]:
        return [step.as_text() for step in self.atomic_actions]

    @classmethod
    def from_record(cls, record: "MemoryRecord") -> ShortcutSpec | None:
        """Dual-read structured specs and older text-only shortcut metadata."""
        if record.kind is not MemoryKind.SHORTCUT:
            return None
        meta = record.metadata or {}
        raw = meta.get("shortcut")
        if isinstance(raw, dict):
            return cls.model_validate(raw)
        actions = _atomic_from_legacy(meta)
        name = meta.get("name") or _name_from_content(record.content)
        return cls(
            name=str(name),
            description=meta.get("description") or record.content.split("\n", 1)[0],
            preconditions=_as_str_list(meta.get("preconditions")),
            arguments=dict(meta.get("arguments") or {}),
            atomic_actions=actions,
            tags=list(record.tags),
            app_ids=list(record.app_ids),
            subgoal=meta.get("subgoal"),
        )


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
    logical_key: str | None = None
    status: RecordStatus = RecordStatus.ACTIVE
    superseded_by: str | None = None
    screen: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    block: str | None = None
    stability: Stability = Stability.STABLE
    success_count: int = 0
    fail_count: int = 0
    content_hash: str | None = None
    signature: str | None = None
    signer: str | None = None

    def is_active(self) -> bool:
        return self.status is RecordStatus.ACTIVE

    def is_promotable(self) -> bool:
        return self.kind in PROMOTABLE_KINDS

    def searchable_text(self) -> str:
        apps = " ".join(self.app_ids)
        tags = " ".join(self.tags)
        extra_bits: list[str] = []
        if self.logical_key:
            extra_bits.append(self.logical_key)
        if self.screen:
            extra_bits.append(self.screen)
        if self.block:
            extra_bits.append(self.block)
        extra_bits.append(self.stability.value)
        extra_bits.extend(self.depends_on)
        if self.kind is MemoryKind.SHORTCUT:
            spec = ShortcutSpec.from_record(self)
            if spec:
                extra_bits.extend(
                    [
                        spec.name,
                        spec.description,
                        " ".join(spec.preconditions),
                        " ".join(spec.action_texts()),
                        " ".join(f"{k} {v}" for k, v in spec.arguments.items()),
                        spec.subgoal or "",
                    ]
                )
        if self.kind is MemoryKind.CAUSAL_ANCHOR:
            evidence = self.metadata.get("evidence") or {}
            if isinstance(evidence, dict):
                extra_bits.extend(str(v) for v in evidence.values() if v)
        extra = " ".join(part for part in extra_bits if part)
        return " ".join(
            part
            for part in (
                self.kind.value,
                self.content,
                self.task_id,
                apps,
                tags,
                extra,
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


def parse_atomic_action(
    action: Any,
    *,
    app_id: str | None = None,
    screen: str | None = None,
) -> AtomicAction:
    """Parse a trajectory action (string or dict) into an AtomicAction."""
    if isinstance(action, AtomicAction):
        return action
    if isinstance(action, dict):
        data = dict(action)
        data.setdefault("app_id", app_id)
        data.setdefault("screen", screen)
        if "type" not in data:
            data["type"] = str(data.get("action") or data.get("name") or "unknown")
        return AtomicAction.model_validate(data)
    text = "" if action is None else str(action).strip()
    if not text:
        return AtomicAction(type="unknown", app_id=app_id, screen=screen)
    if ":" in text:
        typ, rest = text.split(":", 1)
        args: dict[str, Any] = {}
        target = rest.strip() or None
        if "=" in rest and "(" not in rest:
            key, value = rest.split("=", 1)
            if "," not in key and ":" not in key:
                args[key.strip()] = value.strip()
                target = None
        return AtomicAction(
            type=typ.strip() or "unknown",
            target=target,
            args=args,
            app_id=app_id,
            screen=screen,
        )
    return AtomicAction(type=text, app_id=app_id, screen=screen)


def _atomic_from_legacy(meta: dict[str, Any]) -> list[AtomicAction]:
    raw_atomic = meta.get("atomic_actions")
    if isinstance(raw_atomic, list) and raw_atomic:
        out: list[AtomicAction] = []
        for item in raw_atomic:
            if isinstance(item, AtomicAction):
                out.append(item)
            elif isinstance(item, dict):
                out.append(AtomicAction.model_validate(item))
            else:
                out.append(parse_atomic_action(item))
        return out
    return [parse_atomic_action(item) for item in _as_str_list(meta.get("actions"))]


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _name_from_content(content: str) -> str:
    line = (content or "shortcut").split("\n", 1)[0]
    line = line.replace("Shortcut:", "").strip()
    return line[:48] or "shortcut"


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
