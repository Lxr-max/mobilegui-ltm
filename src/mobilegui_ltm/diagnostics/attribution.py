"""Rule-based helped / hurt / unused attribution (no LLM).

Rules:
- success + retrieved shortcut aligned with the trajectory → helped
- failure after injecting a shortcut → hurt
- unused otherwise

Non-shortcut retrieved rows use a weaker alignment check on success
(content overlap with the episode) so failure notes that actually recover
a pass@k retry can count as helped.
"""

from __future__ import annotations

from mobilegui_ltm.diagnostics.schema import (
    Attribution,
    DiagnosticEvent,
    EpisodeTrace,
)
from mobilegui_ltm.encode.text import traj_text
from mobilegui_ltm.retrieve.scoring import tokenize
from mobilegui_ltm.schema import MemoryKind, MemoryRecord, ShortcutSpec

_MIN_OVERLAP = 2


def episode_blob(trace: EpisodeTrace) -> str:
    reason = trace.outcome.reason or ""
    return f"{traj_text(trace.traj)} {reason} {trace.query}"


def record_blob(record: MemoryRecord) -> str:
    parts = [record.content, record.logical_key or "", " ".join(record.tags)]
    if record.kind is MemoryKind.SHORTCUT:
        spec = ShortcutSpec.from_record(record)
        if spec:
            parts.extend(
                [
                    spec.name,
                    spec.description,
                    " ".join(spec.preconditions),
                    " ".join(spec.action_texts()),
                    spec.subgoal or "",
                ]
            )
    return " ".join(part for part in parts if part)


def aligned(record: MemoryRecord, trace: EpisodeTrace, *, min_overlap: int = _MIN_OVERLAP) -> bool:
    """True when record tokens / shortcut steps overlap the episode text."""
    hay = tokenize(episode_blob(trace))
    if not hay:
        return False
    hay_set = set(hay)
    if record.kind is MemoryKind.SHORTCUT:
        spec = ShortcutSpec.from_record(record)
        if spec:
            actions = spec.action_texts()
            traj = episode_blob(trace).lower()
            for action in actions:
                needle = str(action).strip().lower()
                if needle and needle in traj:
                    return True
            if spec.name and spec.name.lower() in traj:
                return True
    needles = tokenize(record_blob(record))
    if not needles:
        return False
    overlap = sum(1 for tok in set(needles) if tok in hay_set and len(tok) > 2)
    return overlap >= min_overlap


def was_injected(record: MemoryRecord, trace: EpisodeTrace) -> bool:
    if not trace.injected and not trace.memories_retrieved:
        return False
    return any(item.id == record.id for item in trace.memories_retrieved)


def attribute_record(record: MemoryRecord, trace: EpisodeTrace) -> Attribution:
    injected = was_injected(record, trace)
    is_shortcut = record.kind is MemoryKind.SHORTCUT
    if trace.success and is_shortcut and aligned(record, trace):
        return Attribution.HELPED
    if trace.failed and is_shortcut and injected:
        return Attribution.HURT
    if trace.success and aligned(record, trace):
        return Attribution.HELPED
    return Attribution.UNUSED


def attribute_episode(trace: EpisodeTrace) -> list[DiagnosticEvent]:
    events: list[DiagnosticEvent] = []
    for record in trace.memories_retrieved:
        label = attribute_record(record, trace)
        reason = _reason(label, record, trace)
        events.append(
            DiagnosticEvent(
                kind="attribution",
                message=reason,
                record_id=record.id,
                logical_key=record.logical_key,
                memory_kind=record.kind,
                attribution=label,
            )
        )
    return events


def _reason(label: Attribution, record: MemoryRecord, trace: EpisodeTrace) -> str:
    kind = record.kind.value
    if label is Attribution.HELPED:
        if record.kind is MemoryKind.SHORTCUT:
            return f"success and retrieved shortcut aligned ({kind})"
        return f"success and retrieved {kind} aligned with episode"
    if label is Attribution.HURT:
        return f"failure after injecting shortcut ({kind})"
    return f"{kind} unused on this attempt"
