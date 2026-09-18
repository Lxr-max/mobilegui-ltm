"""EvalAuditor: contradiction / integrity / stale-candidate scan → quarantine.

Quarantine is a metadata flag (not a hard delete). Runtime retrieve skips
quarantined rows by default. Integrity mismatches are research hooks, not a
security product.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from pydantic import BaseModel, Field

from mobilegui_ltm.diagnostics.schema import (
    DiagnosticEvent,
    MemoryAction,
    MemoryActionType,
    is_quarantined,
)
from mobilegui_ltm.retrieve.scoring import active_only
from mobilegui_ltm.schema import MemoryKind, MemoryRecord, PROMOTABLE_KINDS, RecordStatus, Stability

_NEGATIVE = re.compile(
    r"\b(?:avoid|never(?:\s+tap)?|do not (?:use|tap)|don't (?:use|tap)|ignore)\s+"
    r"([A-Za-z][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_POSITIVE = re.compile(
    r"\b(?:prefer|use|tap|open|choose|always(?:\s+tap)?|select)\s+"
    r"([A-Za-z][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


class AuditReport(BaseModel):
    findings: list[DiagnosticEvent] = Field(default_factory=list)
    actions: list[MemoryAction] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {"contradiction": 0, "integrity_fail": 0, "stale_candidate": 0}
        for event in self.findings:
            if event.kind in out:
                out[event.kind] += 1
        return out

    def format_summary(self) -> str:
        counts = self.counts()
        return (
            f"auditor  contradictions={counts['contradiction']}  "
            f"integrity_fail={counts['integrity_fail']}  "
            f"stale_candidates={counts['stale_candidate']}"
        )


class EvalAuditor:
    """Scan a store namespace for records that should not be retrieved."""

    def scan(self, store: Any) -> AuditReport:
        records = _active_records(store)
        findings: list[DiagnosticEvent] = []
        actions: list[MemoryAction] = []

        findings.extend(_duplicate_keys(records))
        findings.extend(_polarity_conflicts(records))
        findings.extend(_integrity_failures(store, records))
        findings.extend(_stale_candidates(store, records))

        seen: set[str] = set()
        for event in findings:
            target = event.record_id or event.logical_key
            if not target or target in seen:
                continue
            if event.kind in {"contradiction", "integrity_fail", "stale_candidate"}:
                seen.add(target)
                actions.append(
                    MemoryAction(
                        type=MemoryActionType.QUARANTINE,
                        target_id=event.record_id,
                        logical_key=event.logical_key,
                        reason=event.message,
                        auto=True,
                    )
                )
        return AuditReport(findings=findings, actions=actions)


def _active_records(store: Any) -> list[MemoryRecord]:
    lister = getattr(store, "list_memories", None)
    if callable(lister):
        records = list(lister(include_inactive=False))
    else:
        backend = getattr(store, "backend", None)
        agent_id = getattr(store, "agent_id", None)
        records = list(backend.list(agent_id=agent_id)) if backend is not None else []
        records = active_only(records)
    return [record for record in records if record.status is RecordStatus.ACTIVE]


def _duplicate_keys(records: Sequence[MemoryRecord]) -> list[DiagnosticEvent]:
    buckets: dict[tuple[MemoryKind, str], list[MemoryRecord]] = {}
    for record in records:
        if not record.logical_key or is_quarantined(record):
            continue
        buckets.setdefault((record.kind, record.logical_key), []).append(record)
    events: list[DiagnosticEvent] = []
    for (kind, key), group in buckets.items():
        contents = {item.content.strip() for item in group}
        if len(group) < 2 and len(contents) < 2:
            continue
        if len(group) >= 2:
            for item in group[1:]:
                events.append(
                    DiagnosticEvent(
                        kind="contradiction",
                        message=f"duplicate active logical_key {key} ({kind.value})",
                        record_id=item.id,
                        logical_key=key,
                        memory_kind=kind,
                    )
                )
    return events


def _polarity_conflicts(records: Sequence[MemoryRecord]) -> list[DiagnosticEvent]:
    avoided: dict[str, list[MemoryRecord]] = {}
    preferred: dict[str, list[MemoryRecord]] = {}
    for record in records:
        if is_quarantined(record):
            continue
        text = record.content or ""
        for match in _NEGATIVE.finditer(text):
            avoided.setdefault(match.group(1).lower(), []).append(record)
        for match in _POSITIVE.finditer(text):
            preferred.setdefault(match.group(1).lower(), []).append(record)
    events: list[DiagnosticEvent] = []
    for entity, positive_rows in preferred.items():
        negative_rows = avoided.get(entity) or []
        if not negative_rows:
            continue
        # Learned avoidance vs a row that still recommends the entity.
        for record in positive_rows:
            if any(record.id == other.id for other in negative_rows):
                continue
            events.append(
                DiagnosticEvent(
                    kind="contradiction",
                    message=f"polarity conflict on {entity}: avoidance vs recommendation",
                    record_id=record.id,
                    logical_key=record.logical_key,
                    memory_kind=record.kind,
                    metadata={"entity": entity},
                )
            )
    return events


def _integrity_failures(store: Any, records: Sequence[MemoryRecord]) -> list[DiagnosticEvent]:
    guard = getattr(store, "integrity", None)
    if guard is None or not getattr(guard, "enabled", False):
        return []
    events: list[DiagnosticEvent] = []
    checker = getattr(guard, "check", None)
    if not callable(checker):
        return events
    _ok, bad = checker(records)
    for record in bad:
        if is_quarantined(record):
            continue
        events.append(
            DiagnosticEvent(
                kind="integrity_fail",
                message="content hash / signature mismatch",
                record_id=record.id,
                logical_key=record.logical_key,
                memory_kind=record.kind,
            )
        )
    return events


def _stale_candidates(store: Any, records: Sequence[MemoryRecord]) -> list[DiagnosticEvent]:
    demote_after = max(1, int(getattr(store, "demote_after", 2) or 2))
    events: list[DiagnosticEvent] = []
    for record in records:
        if is_quarantined(record):
            continue
        if record.kind not in PROMOTABLE_KINDS:
            continue
        if record.stability is not Stability.CANDIDATE:
            continue
        if record.fail_count >= demote_after and record.success_count == 0:
            events.append(
                DiagnosticEvent(
                    kind="stale_candidate",
                    message=(
                        f"candidate {record.kind.value} failed {record.fail_count} "
                        "times with no success"
                    ),
                    record_id=record.id,
                    logical_key=record.logical_key,
                    memory_kind=record.kind,
                )
            )
    return events
