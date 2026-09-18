"""Shortcut encoder: reusable GUI action snippets from successful attempts.

Inspired by skill / shortcut memories used by multi-agent mobile GUI systems:
after a success (or a clean high-progress partial), persist the action sequence
so a later attempt can reuse it. This stays SDK-side — no planner/worker of our
own — and writes ``MemoryKind.SHORTCUT`` records into the same store as UI facts
and failure notes.
"""

from __future__ import annotations

import re
from typing import Any

from mobilegui_ltm.encode.text import (
    action_text,
    as_text,
    collect_app_ids,
    string_list,
)
from mobilegui_ltm.schema import (
    AttemptOutcome,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    Trajectory,
    TrajectoryStep,
)

_SKILL = re.compile(
    r"(filter|cart|checkout|login|search|apply_|add_to|confirm|submit|pay|wifi|type:)",
    re.I,
)
_DONE_SCREENS = {
    "cart",
    "checkout",
    "done",
    "success",
    "confirm",
    "complete",
    "paid",
    "finish",
}


def _subgoal_label(traj: Trajectory, outcome: AttemptOutcome) -> str | None:
    explicit = string_list(traj.metadata.get("subgoals") or traj.metadata.get("subgoal_traces"))
    if explicit:
        return explicit[0]
    last = traj.steps[-1] if traj.steps else None
    if last and last.screen:
        return last.screen
    if outcome.reason:
        return outcome.reason[:80]
    return None


def _preconditions(traj: Trajectory) -> list[str]:
    pre: list[str] = []
    first = traj.steps[0] if traj.steps else None
    if first and first.app_id:
        pre.append(f"app={first.app_id}")
    elif traj.app_ids:
        pre.append(f"app={traj.app_ids[0]}")
    if first and first.screen:
        pre.append(f"start_screen={first.screen}")
    if first and first.observation:
        obs = as_text(first.observation).strip()
        if obs:
            pre.append(f"start_obs={obs[:120]}")
    for hint in string_list(traj.metadata.get("preconditions")):
        if hint not in pre:
            pre.append(hint)
    return pre


def _actions(steps: list[TrajectoryStep]) -> list[str]:
    out: list[str] = []
    for step in steps:
        text = action_text(step)
        if text:
            out.append(text)
    return out


def _screens(steps: list[TrajectoryStep]) -> list[str]:
    return [step.screen for step in steps if step.screen]


def _summary(actions: list[str], screens: list[str], subgoal: str | None) -> str:
    if subgoal:
        head = subgoal
    elif screens:
        head = " -> ".join(dict.fromkeys(screens))
    elif actions:
        head = " -> ".join(actions[:6])
    else:
        head = "successful GUI snippet"
    return f"Shortcut: {head}"


def _should_emit(outcome: AttemptOutcome, traj: Trajectory) -> bool:
    if outcome.status is OutcomeStatus.SUCCESS:
        return True
    if outcome.status is not OutcomeStatus.PARTIAL:
        return False
    if outcome.metadata.get("emit_shortcuts") or traj.metadata.get("emit_shortcuts"):
        return True
    progress = outcome.metrics.get("progress")
    try:
        if progress is not None and float(progress) >= 0.8:
            return True
    except (TypeError, ValueError):
        pass
    last = traj.steps[-1] if traj.steps else None
    if last and last.screen and last.screen.lower() in _DONE_SCREENS:
        return True
    if last and action_text(last) and _SKILL.search(action_text(last)):
        return len(traj.steps) >= 3
    return False


def _explicit_items(traj: Trajectory) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in traj.metadata.get("shortcuts") or []:
        if isinstance(raw, str) and raw.strip():
            items.append({"summary": raw.strip(), "actions": [], "preconditions": []})
        elif isinstance(raw, dict):
            items.append(raw)
    return items


def _record(
    *,
    summary: str,
    actions: list[str],
    screens: list[str],
    preconditions: list[str],
    subgoal: str | None,
    tags: list[str],
    common: dict[str, Any],
) -> MemoryRecord:
    content_lines = [summary]
    if actions:
        content_lines.append("Actions: " + " -> ".join(actions))
    if preconditions:
        content_lines.append("Preconditions: " + "; ".join(preconditions))
    if subgoal:
        content_lines.append(f"Subgoal: {subgoal}")
    tag_list = list(dict.fromkeys([t for t in tags if t]))
    return MemoryRecord(
        kind=MemoryKind.SHORTCUT,
        content="\n".join(content_lines),
        tags=tag_list,
        metadata={
            "actions": actions,
            "screens": screens,
            "preconditions": preconditions,
            "subgoal": subgoal,
        },
        **common,
    )


class ShortcutEncoder:
    """Mine reusable action snippets from successful (or clean partial) trajectories."""

    def __init__(self, *, max_skill_snippets: int = 3, min_actions: int = 1) -> None:
        self.max_skill_snippets = max_skill_snippets
        self.min_actions = min_actions

    def encode(
        self,
        task_id: str,
        attempt_k: int,
        traj: Trajectory,
        outcome: AttemptOutcome,
        agent_id: str,
    ) -> list[MemoryRecord]:
        if not _should_emit(outcome, traj):
            return []

        app_ids = collect_app_ids(traj)
        subgoal = _subgoal_label(traj, outcome)
        preconditions = _preconditions(traj)
        common = dict(
            task_id=task_id,
            attempt_k=attempt_k,
            agent_id=agent_id,
            app_ids=app_ids,
        )
        quality = "success" if outcome.status is OutcomeStatus.SUCCESS else "partial"
        records: list[MemoryRecord] = []

        for item in _explicit_items(traj):
            actions = string_list(item.get("actions"))
            screens = string_list(item.get("screens"))
            pre = string_list(item.get("preconditions")) or list(preconditions)
            summary_raw = item.get("summary") or item.get("content") or item.get("text")
            summary = (
                str(summary_raw)
                if summary_raw
                else _summary(actions, screens, item.get("subgoal") or subgoal)
            )
            if not summary.lower().startswith("shortcut"):
                summary = f"Shortcut: {summary}"
            records.append(
                _record(
                    summary=summary,
                    actions=actions,
                    screens=screens,
                    preconditions=pre,
                    subgoal=item.get("subgoal") or subgoal,
                    tags=["shortcut", "explicit", quality, task_id]
                    + app_ids
                    + ([subgoal] if subgoal else []),
                    common=common,
                )
            )

        actions = _actions(traj.steps)
        screens = _screens(traj.steps)
        if len(actions) >= self.min_actions:
            records.append(
                _record(
                    summary=_summary(actions, screens, subgoal),
                    actions=actions,
                    screens=screens,
                    preconditions=preconditions,
                    subgoal=subgoal,
                    tags=["shortcut", "episode", quality, task_id]
                    + app_ids
                    + ([subgoal] if subgoal else []),
                    common=common,
                )
            )

        skill_added = 0
        for index, step in enumerate(traj.steps):
            if skill_added >= self.max_skill_snippets:
                break
            act = action_text(step)
            if not act or not _SKILL.search(act):
                continue
            start = max(0, index - 2)
            window = traj.steps[start : index + 1]
            window_actions = _actions(window)
            if len(window_actions) < 2:
                continue
            if actions and window_actions == actions:
                continue
            window_screens = _screens(window)
            skill_subgoal = step.screen or act
            records.append(
                _record(
                    summary=_summary(window_actions, window_screens, skill_subgoal),
                    actions=window_actions,
                    screens=window_screens,
                    preconditions=_preconditions(Trajectory(steps=window, app_ids=app_ids)),
                    subgoal=skill_subgoal,
                    tags=["shortcut", "skill", quality, skill_subgoal]
                    + app_ids,
                    common=common,
                )
            )
            skill_added += 1

        # Drop empty content and exact duplicate action sequences.
        seen: set[tuple[str, ...]] = set()
        unique: list[MemoryRecord] = []
        for record in records:
            key = tuple(record.metadata.get("actions") or [record.content])
            if key in seen:
                continue
            seen.add(key)
            unique.append(record)
        return unique
