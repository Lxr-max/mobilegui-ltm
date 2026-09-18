"""Shortcut encoder: executable GUI action snippets from successful attempts.

Skill-style shortcuts (name, preconditions, arguments, atomic_actions) stay
SDK-side — no planner/worker of our own. Older text-only ``metadata['actions']``
lists are still written for dual-read.
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
    AtomicAction,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    ShortcutSpec,
    Trajectory,
    TrajectoryStep,
    parse_atomic_action,
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
_SLUG = re.compile(r"[^a-z0-9]+")


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


def _atomic_actions(steps: list[TrajectoryStep]) -> list[AtomicAction]:
    out: list[AtomicAction] = []
    for step in steps:
        if step.action is None and not action_text(step):
            continue
        out.append(parse_atomic_action(step.action, app_id=step.app_id, screen=step.screen))
    return [item for item in out if item.type and item.type != "unknown"]


def _screens(steps: list[TrajectoryStep]) -> list[str]:
    return [step.screen for step in steps if step.screen]


def _slug(text: str) -> str:
    slug = _SLUG.sub("_", text.lower()).strip("_")
    return slug[:40] or "shortcut"


def _name(subgoal: str | None, actions: list[AtomicAction], kind: str) -> str:
    if subgoal:
        return _slug(subgoal)
    if actions:
        return _slug(actions[0].as_text())
    return _slug(kind)


def _arguments(actions: list[AtomicAction], preconditions: list[str]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for action in actions:
        for key, value in action.args.items():
            args.setdefault(key, value)
        if action.type.lower() in {"tap", "click"} and action.target:
            if "shop" in action.target.lower() and "seller" not in args:
                args["seller"] = action.target
    for pre in preconditions:
        if pre.startswith("app=") and "app" not in args:
            args["app"] = pre.split("=", 1)[1]
        if pre.startswith("start_screen=") and "screen" not in args:
            args["screen"] = pre.split("=", 1)[1]
    return args


def _summary(spec: ShortcutSpec) -> str:
    head = spec.description or spec.subgoal or spec.name
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


def _spec_to_record(
    spec: ShortcutSpec,
    *,
    quality: str,
    tags: list[str],
    common: dict[str, Any],
    screen: str | None,
) -> MemoryRecord:
    content_lines = [_summary(spec)]
    if spec.description and spec.description not in content_lines[0]:
        content_lines.append(spec.description)
    texts = spec.action_texts()
    if texts:
        content_lines.append("Actions: " + " -> ".join(texts))
    if spec.preconditions:
        content_lines.append("Preconditions: " + "; ".join(spec.preconditions))
    if spec.subgoal:
        content_lines.append(f"Subgoal: {spec.subgoal}")
    if spec.arguments:
        content_lines.append(
            "Arguments: " + ", ".join(f"{k}={v}" for k, v in spec.arguments.items())
        )
    tag_list = list(dict.fromkeys([t for t in tags if t]))
    metadata = {
        "shortcut": spec.model_dump(mode="json"),
        "name": spec.name,
        "description": spec.description,
        "actions": texts,  # dual-read for older inject/tests
        "atomic_actions": [step.model_dump(mode="json") for step in spec.atomic_actions],
        "preconditions": spec.preconditions,
        "arguments": spec.arguments,
        "subgoal": spec.subgoal,
        "screens": [step.screen for step in spec.atomic_actions if step.screen],
    }
    return MemoryRecord(
        kind=MemoryKind.SHORTCUT,
        content="\n".join(content_lines),
        tags=tag_list,
        logical_key=f"shortcut:{spec.name}",
        screen=screen,
        metadata=metadata,
        **common,
    )


def _spec_from_steps(
    steps: list[TrajectoryStep],
    *,
    name: str,
    description: str,
    preconditions: list[str],
    subgoal: str | None,
    app_ids: list[str],
    extra_tags: list[str],
) -> ShortcutSpec | None:
    atomic = _atomic_actions(steps)
    if not atomic:
        return None
    return ShortcutSpec(
        name=name,
        description=description,
        preconditions=preconditions,
        arguments=_arguments(atomic, preconditions),
        atomic_actions=atomic,
        tags=extra_tags,
        app_ids=app_ids,
        subgoal=subgoal,
    )


class ShortcutEncoder:
    """Mine executable action snippets from successful (or clean partial) trajectories."""

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
        start_screen = traj.steps[0].screen if traj.steps else None

        for item in _explicit_items(traj):
            if item.get("atomic_actions") or item.get("name"):
                spec = ShortcutSpec.model_validate(
                    {
                        "name": item.get("name") or _slug(str(item.get("summary") or "explicit")),
                        "description": item.get("description") or item.get("summary") or "",
                        "preconditions": string_list(item.get("preconditions")) or list(preconditions),
                        "arguments": item.get("arguments") or {},
                        "atomic_actions": item.get("atomic_actions")
                        or [parse_atomic_action(a).model_dump() for a in string_list(item.get("actions"))],
                        "tags": string_list(item.get("tags")),
                        "app_ids": app_ids,
                        "subgoal": item.get("subgoal") or subgoal,
                    }
                )
            else:
                atomic = [parse_atomic_action(a) for a in string_list(item.get("actions"))]
                summary = str(item.get("summary") or item.get("content") or item.get("text") or "explicit")
                spec = ShortcutSpec(
                    name=_slug(summary),
                    description=summary,
                    preconditions=string_list(item.get("preconditions")) or list(preconditions),
                    arguments=dict(item.get("arguments") or {}),
                    atomic_actions=atomic,
                    tags=["explicit"],
                    app_ids=app_ids,
                    subgoal=item.get("subgoal") or subgoal,
                )
            records.append(
                _spec_to_record(
                    spec,
                    quality=quality,
                    tags=["shortcut", "explicit", quality, task_id] + app_ids,
                    common=common,
                    screen=start_screen,
                )
            )

        episode = _spec_from_steps(
            traj.steps,
            name=_name(subgoal, _atomic_actions(traj.steps), "episode"),
            description=subgoal or "successful GUI snippet",
            preconditions=preconditions,
            subgoal=subgoal,
            app_ids=app_ids,
            extra_tags=["episode", quality],
        )
        if episode and len(episode.atomic_actions) >= self.min_actions:
            records.append(
                _spec_to_record(
                    episode,
                    quality=quality,
                    tags=["shortcut", "episode", quality, task_id]
                    + app_ids
                    + ([subgoal] if subgoal else []),
                    common=common,
                    screen=start_screen,
                )
            )

        skill_added = 0
        full_texts = episode.action_texts() if episode else []
        for index, step in enumerate(traj.steps):
            if skill_added >= self.max_skill_snippets:
                break
            act = action_text(step)
            if not act or not _SKILL.search(act):
                continue
            start = max(0, index - 2)
            window = traj.steps[start : index + 1]
            skill_subgoal = step.screen or act
            skill = _spec_from_steps(
                window,
                name=_name(skill_subgoal, _atomic_actions(window), "skill"),
                description=f"skill:{skill_subgoal}",
                preconditions=_preconditions(Trajectory(steps=window, app_ids=app_ids)),
                subgoal=skill_subgoal,
                app_ids=app_ids,
                extra_tags=["skill", quality],
            )
            if skill is None or len(skill.atomic_actions) < 2:
                continue
            if skill.action_texts() == full_texts:
                continue
            records.append(
                _spec_to_record(
                    skill,
                    quality=quality,
                    tags=["shortcut", "skill", quality, skill_subgoal] + app_ids,
                    common=common,
                    screen=window[0].screen if window else step.screen,
                )
            )
            skill_added += 1

        seen: set[tuple[str, ...]] = set()
        unique: list[MemoryRecord] = []
        for record in records:
            spec = ShortcutSpec.from_record(record)
            key = tuple(spec.action_texts() if spec else [record.content])
            if key in seen:
                continue
            seen.add(key)
            unique.append(record)
        return unique
