"""Heuristic trajectory → UI facts / subgoal traces / failure notes.

No LLM is required. Structured hints in ``Trajectory.metadata`` and
``AttemptOutcome.reason`` are copied through; remaining text is mined for
app ids, seller-like names, filters, and last-screen progress.

Shortcuts (reusable action snippets) are a phase-2 extension and are not
emitted by the default encoder.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from mobilegui_ltm.schema import (
    AttemptOutcome,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    Trajectory,
    TrajectoryStep,
)

_SHOP = re.compile(r"\bShop[A-Za-z0-9]+\b")
_SIZE = re.compile(r"\bsize\s*[=:]?\s*(\d+)\b", re.I)
_FILTER = re.compile(r"\bfilter(?:ed|s)?\s*[:=]?\s*([A-Za-z0-9_ \-]{1,40})", re.I)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            parts.append(f"{key}: {_as_text(item)}")
        return " ".join(parts)
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(item) for item in value)
    return str(value)


def _step_text(step: TrajectoryStep) -> str:
    return " ".join(
        part
        for part in (
            _as_text(step.observation),
            _as_text(step.action),
            step.app_id or "",
            step.screen or "",
            _as_text(step.extras),
        )
        if part
    )


def _traj_text(traj: Trajectory) -> str:
    chunks = [_as_text(traj.metadata)]
    for step in traj.steps:
        chunks.append(_step_text(step))
    return " ".join(chunks)


def _string_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


class TrajectorySummarizer:
    """Default encoder: structured JSON memories, no embeddings, no graph."""

    def __init__(self, *, emit_shortcuts: bool = False) -> None:
        self.emit_shortcuts = emit_shortcuts
        self._shortcut_encoder = ShortcutEncoder()

    def encode(
        self,
        task_id: str,
        attempt_k: int,
        traj: Trajectory,
        outcome: AttemptOutcome,
        agent_id: str,
    ) -> list[MemoryRecord]:
        app_ids = list(traj.app_ids)
        for step in traj.steps:
            if step.app_id and step.app_id not in app_ids:
                app_ids.append(step.app_id)
        for extra in _string_list(traj.metadata.get("app_ids") or traj.metadata.get("apps")):
            if extra not in app_ids:
                app_ids.append(extra)

        common = dict(
            task_id=task_id,
            attempt_k=attempt_k,
            agent_id=agent_id,
            app_ids=app_ids,
        )
        records: list[MemoryRecord] = []

        for fact in _string_list(traj.metadata.get("ui_facts")):
            records.append(
                MemoryRecord(kind=MemoryKind.UI_FACT, content=fact, tags=["explicit"], **common)
            )
        records.extend(self._heuristic_ui_facts(traj, outcome, common))

        for subgoal in _string_list(traj.metadata.get("subgoals") or traj.metadata.get("subgoal_traces")):
            records.append(
                MemoryRecord(
                    kind=MemoryKind.SUBGOAL_TRACE,
                    content=subgoal,
                    tags=["explicit"],
                    **common,
                )
            )
        records.append(self._subgoal_from_progress(traj, outcome, common))

        for note in _string_list(traj.metadata.get("failure_notes")):
            records.append(
                MemoryRecord(
                    kind=MemoryKind.FAILURE_NOTE,
                    content=note,
                    tags=["explicit"],
                    **common,
                )
            )
        notes = _string_list(outcome.metadata.get("failure_notes"))
        for note in notes:
            records.append(
                MemoryRecord(
                    kind=MemoryKind.FAILURE_NOTE,
                    content=note,
                    tags=["outcome"],
                    **common,
                )
            )
        if outcome.status != OutcomeStatus.SUCCESS:
            records.extend(self._failure_notes(traj, outcome, common))

        if self.emit_shortcuts:
            records.extend(
                self._shortcut_encoder.encode(task_id, attempt_k, traj, outcome, agent_id)
            )

        return [r for r in records if r.content and r.content.strip()]

    def _heuristic_ui_facts(
        self,
        traj: Trajectory,
        outcome: AttemptOutcome,
        common: dict[str, Any],
    ) -> list[MemoryRecord]:
        blob = f"{_traj_text(traj)} {outcome.reason or ''}"
        facts: list[MemoryRecord] = []
        if common["app_ids"]:
            facts.append(
                MemoryRecord(
                    kind=MemoryKind.UI_FACT,
                    content=f"Apps visited: {', '.join(common['app_ids'])}",
                    tags=["apps"],
                    **common,
                )
            )
        shops = list(dict.fromkeys(_SHOP.findall(blob)))
        if shops:
            facts.append(
                MemoryRecord(
                    kind=MemoryKind.UI_FACT,
                    content=f"Sellers / shop entities observed: {', '.join(shops)}",
                    tags=["entity", "shop"],
                    **common,
                )
            )
        sizes = list(dict.fromkeys(_SIZE.findall(blob)))
        if sizes:
            facts.append(
                MemoryRecord(
                    kind=MemoryKind.UI_FACT,
                    content=f"Size values mentioned: {', '.join(sizes)}",
                    tags=["filter", "size"],
                    **common,
                )
            )
        filters = list(dict.fromkeys(m.strip() for m in _FILTER.findall(blob) if m.strip()))
        if filters:
            facts.append(
                MemoryRecord(
                    kind=MemoryKind.UI_FACT,
                    content=f"Filters mentioned: {', '.join(filters)}",
                    tags=["filter"],
                    **common,
                )
            )
        screens = [s.screen for s in traj.steps if s.screen]
        if screens:
            facts.append(
                MemoryRecord(
                    kind=MemoryKind.UI_FACT,
                    content=f"Screens traversed: {' -> '.join(screens)}",
                    tags=["screen"],
                    **common,
                )
            )
        return facts

    def _subgoal_from_progress(
        self,
        traj: Trajectory,
        outcome: AttemptOutcome,
        common: dict[str, Any],
    ) -> MemoryRecord:
        last = traj.steps[-1] if traj.steps else None
        status = outcome.status.value
        if last is None:
            content = (
                f"No GUI steps recorded on attempt {common['attempt_k']} "
                f"(outcome={status})."
            )
        else:
            content = (
                f"Attempt {common['attempt_k']} ended {status} at screen="
                f"{last.screen or 'unknown'} after action={_as_text(last.action) or 'unknown'} "
                f"({len(traj.steps)} steps). "
                f"Last observation: {_as_text(last.observation)[:240]}"
            )
        return MemoryRecord(
            kind=MemoryKind.SUBGOAL_TRACE,
            content=content,
            tags=["progress", status],
            **common,
        )

    def _failure_notes(
        self,
        traj: Trajectory,
        outcome: AttemptOutcome,
        common: dict[str, Any],
    ) -> list[MemoryRecord]:
        notes: list[MemoryRecord] = []
        if outcome.reason:
            notes.append(
                MemoryRecord(
                    kind=MemoryKind.FAILURE_NOTE,
                    content=outcome.reason,
                    tags=["reason"],
                    **common,
                )
            )
        last = traj.steps[-1] if traj.steps else None
        last_action = _as_text(last.action) if last else ""
        last_obs = _as_text(last.observation) if last else ""
        blob = f"{outcome.reason or ''} {last_obs} {last_action}"
        shops = list(dict.fromkeys(_SHOP.findall(blob)))
        if shops:
            avoid = shops[0]
            alt = shops[1] if len(shops) > 1 else None
            retry = f"Avoid {avoid} on retry."
            if alt:
                retry += f" Prefer {alt} if it appeared in search results."
            if _SIZE.search(blob):
                size = _SIZE.search(blob)
                assert size is not None
                retry += (
                    f" Apply a size {size.group(1)} filter before opening a product."
                )
            notes.append(
                MemoryRecord(
                    kind=MemoryKind.FAILURE_NOTE,
                    content=retry,
                    tags=["avoidance"],
                    **common,
                )
            )
        elif last_action:
            notes.append(
                MemoryRecord(
                    kind=MemoryKind.FAILURE_NOTE,
                    content=f"On retry, avoid repeating failed action: {last_action}",
                    tags=["avoidance"],
                    **common,
                )
            )
        elif last_obs:
            notes.append(
                MemoryRecord(
                    kind=MemoryKind.FAILURE_NOTE,
                    content=f"Attempt failed. Last observation: {last_obs}",
                    tags=["avoidance"],
                    **common,
                )
            )
        if not notes:
            notes.append(
                MemoryRecord(
                    kind=MemoryKind.FAILURE_NOTE,
                    content=(
                        f"Attempt {common['attempt_k']} ended "
                        f"{outcome.status.value} without a detailed reason."
                    ),
                    tags=["avoidance"],
                    **common,
                )
            )
        return notes


class ShortcutEncoder:
    """Phase-2 stub: reusable GUI action snippets.

    Returns no records in the MVP. Wire this in when a snippet miner exists.
    """

    def encode(
        self,
        task_id: str,
        attempt_k: int,
        traj: Trajectory,
        outcome: AttemptOutcome,
        agent_id: str,
    ) -> list[MemoryRecord]:
        return []
