"""Heuristic trajectory → UI facts / subgoal traces / failure notes / shortcuts.

No LLM is required. Structured hints in ``Trajectory.metadata`` and
``AttemptOutcome.reason`` are copied through; remaining text is mined for
app ids, seller-like names, filters, and last-screen progress.

Shortcuts are first-class: successful (and clean partial) attempts emit
reusable action snippets via :class:`ShortcutEncoder`.
"""

from __future__ import annotations

import re
from typing import Any

from mobilegui_ltm.encode.anchors import CausalAnchorEncoder
from mobilegui_ltm.encode.shortcuts import ShortcutEncoder
from mobilegui_ltm.encode.text import as_text, collect_app_ids, string_list, traj_text
from mobilegui_ltm.schema import (
    AttemptOutcome,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    Trajectory,
)

__all__ = ["CausalAnchorEncoder", "ShortcutEncoder", "TrajectorySummarizer"]

_SHOP = re.compile(r"\bShop[A-Za-z0-9]+\b")
_SIZE = re.compile(r"\bsize\s*[=:]?\s*(\d+)\b", re.I)
_FILTER = re.compile(r"\bfilter(?:ed|s)?\s*[:=]?\s*([A-Za-z0-9_ \-]{1,40})", re.I)


class TrajectorySummarizer:
    """Default encoder: structured JSON memories (optional hashing embeddings later)."""

    def __init__(self, *, emit_shortcuts: bool = True, emit_anchors: bool = True) -> None:
        self.emit_shortcuts = emit_shortcuts
        self.emit_anchors = emit_anchors
        self._shortcut_encoder = ShortcutEncoder()
        self._anchor_encoder = CausalAnchorEncoder()

    def encode(
        self,
        task_id: str,
        attempt_k: int,
        traj: Trajectory,
        outcome: AttemptOutcome,
        agent_id: str,
    ) -> list[MemoryRecord]:
        app_ids = collect_app_ids(traj)
        common = dict(
            task_id=task_id,
            attempt_k=attempt_k,
            agent_id=agent_id,
            app_ids=app_ids,
        )
        records: list[MemoryRecord] = []

        for fact in traj.metadata.get("ui_facts") or []:
            records.append(self._explicit_ui_fact(fact, common))
        records.extend(self._heuristic_ui_facts(traj, outcome, common))

        for subgoal in string_list(traj.metadata.get("subgoals") or traj.metadata.get("subgoal_traces")):
            records.append(
                MemoryRecord(
                    kind=MemoryKind.SUBGOAL_TRACE,
                    content=subgoal,
                    tags=["explicit"],
                    logical_key=f"{task_id}::subgoal:named:{subgoal[:40]}",
                    screen=traj.steps[-1].screen if traj.steps else None,
                    **common,
                )
            )
        records.append(self._subgoal_from_progress(traj, outcome, common))

        for note in string_list(traj.metadata.get("failure_notes")):
            records.append(
                MemoryRecord(
                    kind=MemoryKind.FAILURE_NOTE,
                    content=note,
                    tags=["explicit"],
                    **common,
                )
            )
        notes = string_list(outcome.metadata.get("failure_notes"))
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
        if self.emit_anchors:
            records.extend(
                self._anchor_encoder.encode(task_id, attempt_k, traj, outcome, agent_id)
            )

        return [r for r in records if r.content and r.content.strip()]

    def _explicit_ui_fact(self, fact: Any, common: dict[str, Any]) -> MemoryRecord:
        if isinstance(fact, dict):
            content = str(fact.get("content") or fact.get("value") or fact.get("text") or "")
            key = fact.get("key") or f"ui:explicit:{content[:32]}"
            screen = fact.get("screen")
            return MemoryRecord(
                kind=MemoryKind.UI_FACT,
                content=content,
                tags=["explicit"],
                logical_key=str(key),
                screen=screen,
                **common,
            )
        text = str(fact)
        return MemoryRecord(
            kind=MemoryKind.UI_FACT,
            content=text,
            tags=["explicit"],
            logical_key=f"ui:explicit:{text[:40]}",
            **common,
        )

    def _heuristic_ui_facts(
        self,
        traj: Trajectory,
        outcome: AttemptOutcome,
        common: dict[str, Any],
    ) -> list[MemoryRecord]:
        blob = f"{traj_text(traj)} {outcome.reason or ''}"
        facts: list[MemoryRecord] = []
        last_screen = traj.steps[-1].screen if traj.steps else None
        if common["app_ids"]:
            facts.append(
                MemoryRecord(
                    kind=MemoryKind.UI_FACT,
                    content=f"Apps visited: {', '.join(common['app_ids'])}",
                    tags=["apps"],
                    logical_key="ui:apps",
                    screen=last_screen,
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
                    logical_key="ui:sellers",
                    screen=last_screen,
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
                    logical_key="ui:size",
                    screen=last_screen,
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
                    logical_key="ui:filters",
                    screen=last_screen,
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
                    logical_key="ui:screens",
                    screen=screens[-1],
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
                f"{last.screen or 'unknown'} after action={as_text(last.action) or 'unknown'} "
                f"({len(traj.steps)} steps). "
                f"Last observation: {as_text(last.observation)[:240]}"
            )
        return MemoryRecord(
            kind=MemoryKind.SUBGOAL_TRACE,
            content=content,
            tags=["progress", status],
            logical_key=f"{common['task_id']}::subgoal:progress",
            screen=last.screen if last else None,
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
        last_action = as_text(last.action) if last else ""
        last_obs = as_text(last.observation) if last else ""
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
