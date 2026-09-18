"""Kind weights, hard filters, precondition soft-match, 1-hop link expand."""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from mobilegui_ltm.schema import MemoryKind, MemoryRecord, RecordStatus, ShortcutSpec, Stability

_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in _TOKEN.findall(text or "")]

DEFAULT_KIND_WEIGHTS: dict[str, float] = {
    MemoryKind.FAILURE_NOTE.value: 1.15,
    MemoryKind.UI_FACT.value: 1.0,
    MemoryKind.SUBGOAL_TRACE.value: 0.9,
    MemoryKind.SHORTCUT.value: 1.1,
    MemoryKind.CAUSAL_ANCHOR.value: 1.05,
    MemoryKind.APP_PRIOR.value: 0.85,
}


def normalize_kind_weights(
    weights: dict[str, float] | dict[MemoryKind, float] | None,
) -> dict[str, float]:
    merged = dict(DEFAULT_KIND_WEIGHTS)
    if not weights:
        return merged
    for key, value in weights.items():
        merged[key.value if isinstance(key, MemoryKind) else str(key)] = float(value)
    return merged


def kind_weight(
    record: MemoryRecord,
    weights: dict[str, float] | None = None,
) -> float:
    table = weights or DEFAULT_KIND_WEIGHTS
    return float(table.get(record.kind.value, table.get(str(record.kind), 1.0)))


def active_only(records: Iterable[MemoryRecord]) -> list[MemoryRecord]:
    return [r for r in records if r.status is RecordStatus.ACTIVE]


def record_screens(record: MemoryRecord) -> list[str]:
    screens: list[str] = []
    if record.screen:
        screens.append(record.screen)
    for item in record.metadata.get("screens") or []:
        if item and str(item) not in screens:
            screens.append(str(item))
    evidence = record.metadata.get("evidence") or {}
    if isinstance(evidence, dict) and evidence.get("screen"):
        if evidence["screen"] not in screens:
            screens.append(str(evidence["screen"]))
    for pre in record.metadata.get("preconditions") or []:
        text = str(pre)
        if text.startswith("start_screen="):
            value = text.split("=", 1)[1]
            if value and value not in screens:
                screens.append(value)
    return screens


def apply_hard_filters(
    records: Sequence[MemoryRecord],
    *,
    app_ids: Sequence[str] | None = None,
    screen: str | None = None,
    task_id: str | None = None,
    strict_task: bool = False,
) -> list[MemoryRecord]:
    wanted_apps = set(app_ids) if app_ids else None
    out: list[MemoryRecord] = []
    for record in records:
        if wanted_apps is not None:
            if record.app_ids and wanted_apps.isdisjoint(record.app_ids):
                continue
        if screen:
            have = record_screens(record)
            if have and screen not in have:
                continue
        if strict_task and task_id is not None and record.task_id != task_id:
            continue
        out.append(record)
    return out


def _state_text(state: Any) -> str:
    if state is None:
        return ""
    if isinstance(state, str):
        return state
    if isinstance(state, dict):
        parts = []
        for key, value in state.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                parts.append(f"{key} " + " ".join(str(v) for v in value))
            else:
                parts.append(f"{key} {value}")
        return " ".join(parts)
    return str(state)


def precondition_bonus(
    record: MemoryRecord,
    query: str,
    state: Any = None,
) -> float:
    """Soft match shortcut preconditions against the query / current GUI state."""
    if record.kind is not MemoryKind.SHORTCUT:
        return 1.0
    spec = ShortcutSpec.from_record(record)
    pres = list(spec.preconditions) if spec else list(record.metadata.get("preconditions") or [])
    if not pres:
        return 1.0
    haystack = tokenize(f"{query} {_state_text(state)}")
    if not haystack:
        return 1.0
    hay = set(haystack)
    needles = tokenize(" ".join(pres))
    if not needles:
        return 1.0
    hits = sum(1 for tok in needles if tok in hay)
    return 1.0 + 0.12 * hits


def apply_stability_filter(
    records: Iterable[MemoryRecord],
    *,
    include_candidates: bool = True,
    include_retired: bool = False,
) -> list[MemoryRecord]:
    out: list[MemoryRecord] = []
    for record in records:
        if record.stability is Stability.RETIRED and not include_retired:
            continue
        if record.stability is Stability.CANDIDATE and not include_candidates:
            continue
        out.append(record)
    return out


def stability_bonus(record: MemoryRecord, *, prefer_stable: bool = True) -> float:
    if not prefer_stable:
        return 1.0
    if record.stability is Stability.STABLE:
        return 1.15
    if record.stability is Stability.RETIRED:
        return 0.4
    return 1.0


def expand_links(
    hits: Sequence[MemoryRecord],
    pool: Sequence[MemoryRecord],
    *,
    hops: int = 1,
) -> list[MemoryRecord]:
    """Append records linked by id or logical_key (1 hop by default). No graph DB."""
    if hops <= 0 or not hits:
        return list(hits)
    by_id = {record.id: record for record in pool}
    by_key: dict[str, MemoryRecord] = {}
    for record in pool:
        if record.logical_key and record.logical_key not in by_key:
            by_key[record.logical_key] = record
    ordered = list(hits)
    seen = {record.id for record in ordered}
    frontier = list(hits)
    for _ in range(hops):
        nxt: list[MemoryRecord] = []
        for record in frontier:
            refs = list(record.depends_on) + list(record.metadata.get("links") or [])
            for ref in refs:
                target = by_id.get(str(ref)) or by_key.get(str(ref))
                if target is None or not target.is_active() or target.id in seen:
                    continue
                seen.add(target.id)
                ordered.append(target)
                nxt.append(target)
        frontier = nxt
        if not frontier:
            break
    return ordered
