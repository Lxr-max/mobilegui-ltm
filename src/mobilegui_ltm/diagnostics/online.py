"""OnlineDiagnostics: score an episode, propose conservative memory actions.

``apply(..., dry_run=True)`` is the default. Reflector rewrite proposals are
attached to the report but are not applied unless ``include_reflector=True``.
"""

from __future__ import annotations

from typing import Any, Sequence

from mobilegui_ltm.diagnostics.attribution import attribute_episode
from mobilegui_ltm.diagnostics.auditor import EvalAuditor
from mobilegui_ltm.diagnostics.failure_taxonomy import classify_failure
from mobilegui_ltm.diagnostics.outcome import NullOutcomeProvider, OutcomeProvider
from mobilegui_ltm.diagnostics.reflector import EpisodeReflector, NullEpisodeReflector
from mobilegui_ltm.diagnostics.schema import (
    AUTO_ACTION_TYPES,
    REFLECTOR_ACTION_TYPES,
    Attribution,
    DiagnosticEvent,
    DiagnosticReport,
    EpisodeTrace,
    MemoryAction,
    MemoryActionType,
)
from mobilegui_ltm.schema import (
    PROMOTABLE_KINDS,
    AttemptOutcome,
    MemoryRecord,
    coerce_outcome,
)


class OnlineDiagnostics:
    """SDK-only runtime diagnostics. Optional LLM reflector is a plugin only."""

    def __init__(
        self,
        store: Any | None = None,
        *,
        outcome_provider: OutcomeProvider | None = None,
        reflector: EpisodeReflector | None = None,
        auditor: EvalAuditor | None = None,
        auto_audit: bool = True,
    ) -> None:
        self.store = store
        self.outcome_provider: OutcomeProvider = outcome_provider or NullOutcomeProvider()
        self.reflector: EpisodeReflector = reflector or NullEpisodeReflector()
        self.auditor = auditor or EvalAuditor()
        self.auto_audit = auto_audit
        self.history: list[DiagnosticReport] = []

    def bind(self, store: Any) -> "OnlineDiagnostics":
        self.store = store
        return self

    def on_episode_end(self, trace: EpisodeTrace) -> DiagnosticReport:
        """Attribute retrieved memories and propose conservative actions."""
        trace = self._maybe_override_outcome(trace)
        failure_class = classify_failure(trace=trace)
        attributions = attribute_episode(trace)
        events: list[DiagnosticEvent] = [
            DiagnosticEvent(
                kind="failure_class",
                message=failure_class.value,
                failure_class=failure_class,
            )
        ]
        if trace.recovery_delta:
            events.append(
                DiagnosticEvent(
                    kind="recovery_delta",
                    message="failure→success across attempts",
                )
            )
        actions = _propose_actions(trace, attributions)
        audit_findings: list[DiagnosticEvent] = []
        if self.auto_audit and self.store is not None:
            audit = self.auditor.scan(self.store)
            audit_findings = list(audit.findings)
            actions = _merge_actions(actions, audit.actions)

        report = DiagnosticReport(
            task_id=trace.task_id,
            attempt_k=trace.attempt_k,
            outcome=trace.outcome,
            failure_class=failure_class,
            attributions=attributions,
            actions=actions,
            events=events,
            audit_findings=audit_findings,
            recovery_delta=trace.recovery_delta,
            dry_run=True,
            applied=False,
        )
        report.reflector_actions = list(self.reflector.reflect(trace, report))
        self.history.append(report)
        if self.store is not None:
            self.store.last_report = report
        return report

    def apply(
        self,
        actions: Sequence[MemoryAction] | DiagnosticReport | None = None,
        *,
        dry_run: bool = True,
        include_reflector: bool = False,
    ) -> list[dict[str, Any]]:
        """Apply (or preview) promote / demote / quarantine. Default dry-run.

        Reflector rewrite proposals are skipped unless ``include_reflector``.
        """
        pending = self._select_actions(actions, include_reflector=include_reflector)
        results: list[dict[str, Any]] = []
        for action in pending:
            if dry_run:
                results.append(
                    {
                        "type": action.type.value,
                        "target": action.target(),
                        "reason": action.reason,
                        "applied": False,
                        "dry_run": True,
                    }
                )
                continue
            applied = self._apply_one(action)
            results.append(
                {
                    "type": action.type.value,
                    "target": action.target(),
                    "reason": action.reason,
                    "applied": applied is not None,
                    "dry_run": False,
                    "record_id": getattr(applied, "id", None),
                }
            )
        if actions is not None and isinstance(actions, DiagnosticReport):
            actions.dry_run = dry_run
            actions.applied = not dry_run
        elif not dry_run:
            for report in self.history:
                report.dry_run = False
                report.applied = True
        return results

    def format_history(self, *, dry_run: bool = True) -> str:
        lines = [
            f"OnlineDiagnostics  episodes={len(self.history)}  "
            f"apply={'dry-run' if dry_run else 'apply'}"
        ]
        helped = hurt = unused = 0
        promotes: list[MemoryAction] = []
        demotes: list[MemoryAction] = []
        quarantines: list[MemoryAction] = []
        for report in self.history:
            lines.append(report.format_summary())
            counts = report.attribution_counts()
            helped += counts["helped"]
            hurt += counts["hurt"]
            unused += counts["unused"]
            promotes.extend(report.actions_of(MemoryActionType.PROMOTE))
            demotes.extend(report.actions_of(MemoryActionType.DEMOTE))
            quarantines.extend(report.actions_of(MemoryActionType.QUARANTINE))
        lines.append(
            f"attribution totals  helped={helped} hurt={hurt} unused={unused}"
        )
        lines.append("promote proposals:")
        lines.extend(_format_action_lines(promotes) or ["  (none)"])
        lines.append("demote proposals:")
        lines.extend(_format_action_lines(demotes) or ["  (none)"])
        lines.append("quarantine proposals:")
        lines.extend(_format_action_lines(quarantines) or ["  (none)"])
        reflector_n = sum(len(report.reflector_actions) for report in self.history)
        lines.append(
            f"reflector  proposals={reflector_n}  "
            "(NullEpisodeReflector unless a plugin is bound; not applied by default)"
        )
        return "\n".join(lines)

    def _maybe_override_outcome(self, trace: EpisodeTrace) -> EpisodeTrace:
        extra = self.outcome_provider.evaluate(trace)
        if extra is None:
            return trace
        outcome = coerce_outcome(extra)
        if outcome == trace.outcome:
            return trace
        return trace.model_copy(update={"outcome": outcome})

    def _select_actions(
        self,
        actions: Sequence[MemoryAction] | DiagnosticReport | None,
        *,
        include_reflector: bool,
    ) -> list[MemoryAction]:
        if actions is None:
            pending: list[MemoryAction] = []
            for report in self.history:
                pending.extend(report.actions)
                if include_reflector:
                    pending.extend(report.reflector_actions)
        elif isinstance(actions, DiagnosticReport):
            pending = list(actions.actions)
            if include_reflector:
                pending.extend(actions.reflector_actions)
        else:
            pending = list(actions)
        if not include_reflector:
            pending = [
                action
                for action in pending
                if action.type in AUTO_ACTION_TYPES or action.auto
            ]
            pending = [
                action for action in pending if action.type not in REFLECTOR_ACTION_TYPES
            ]
        return _dedupe_actions(pending)

    def _apply_one(self, action: MemoryAction) -> MemoryRecord | None:
        store = self.store
        if store is None:
            raise RuntimeError(
                "OnlineDiagnostics.apply(dry_run=False) needs a bound MemoryStore."
            )
        target = action.target()
        if not target:
            return None
        if action.type is MemoryActionType.PROMOTE:
            return store.promote(target)
        if action.type is MemoryActionType.DEMOTE:
            retire = bool(action.payload.get("retire"))
            return store.demote(target, retire=retire)
        if action.type is MemoryActionType.QUARANTINE:
            return store.quarantine(target, reason=action.reason)
        if action.type is MemoryActionType.UNQUARANTINE:
            return store.unquarantine(target)
        if action.type in REFLECTOR_ACTION_TYPES:
            content = action.payload.get("content")
            if not content:
                return None
            return store.update(target, content=str(content))
        return None


def _propose_actions(
    trace: EpisodeTrace,
    attributions: Sequence[DiagnosticEvent],
) -> list[MemoryAction]:
    by_id = {record.id: record for record in trace.memories_retrieved}
    actions: list[MemoryAction] = []
    for event in attributions:
        record = by_id.get(event.record_id or "")
        if record is None or record.kind not in PROMOTABLE_KINDS:
            continue
        if event.attribution is Attribution.HELPED and trace.success:
            reason = "retrieved shortcut/anchor helped"
            if trace.recovery_delta:
                reason += "; failure→success delta"
            actions.append(
                MemoryAction(
                    type=MemoryActionType.PROMOTE,
                    target_id=record.id,
                    logical_key=record.logical_key,
                    reason=reason,
                    auto=True,
                )
            )
        elif event.attribution is Attribution.HURT and trace.failed:
            actions.append(
                MemoryAction(
                    type=MemoryActionType.DEMOTE,
                    target_id=record.id,
                    logical_key=record.logical_key,
                    reason="failure after injecting shortcut/anchor",
                    auto=True,
                    payload={"retire": record.fail_count >= 1},
                )
            )
    return _dedupe_actions(actions)


def _merge_actions(
    primary: Sequence[MemoryAction], extra: Sequence[MemoryAction]
) -> list[MemoryAction]:
    return _dedupe_actions(list(primary) + list(extra))


def _dedupe_actions(actions: Sequence[MemoryAction]) -> list[MemoryAction]:
    seen: set[tuple[str, str | None]] = set()
    out: list[MemoryAction] = []
    for action in actions:
        key = (action.type.value, action.target())
        if key in seen:
            continue
        seen.add(key)
        out.append(action)
    return out


def _format_action_lines(actions: Sequence[MemoryAction]) -> list[str]:
    lines: list[str] = []
    for action in actions:
        target = action.logical_key or action.target_id or "?"
        reason = f"  ({action.reason})" if action.reason else ""
        lines.append(f"  {action.type.value} {target}{reason}")
    return lines


def resolve_diagnostics(
    value: bool | OnlineDiagnostics | None,
    store: Any,
    *,
    outcome_provider: OutcomeProvider | None = None,
    reflector: EpisodeReflector | None = None,
) -> OnlineDiagnostics | None:
    if value is None or value is False:
        return None
    if value is True:
        return OnlineDiagnostics(
            store,
            outcome_provider=outcome_provider,
            reflector=reflector,
        )
    value.bind(store)
    if outcome_provider is not None:
        value.outcome_provider = outcome_provider
    if reflector is not None:
        value.reflector = reflector
    return value


def trace_from_write(
    *,
    task_id: str,
    attempt_k: int,
    agent_id: str,
    traj: Any,
    outcome: AttemptOutcome,
    memories_retrieved: Sequence[MemoryRecord] | None = None,
    memories_written: Sequence[MemoryRecord] | None = None,
    query: str = "",
    previous_outcome: AttemptOutcome | None = None,
) -> EpisodeTrace:
    retrieved = list(memories_retrieved or [])
    return EpisodeTrace(
        task_id=task_id,
        attempt_k=attempt_k,
        agent_id=agent_id,
        query=query or "",
        traj=traj,
        outcome=outcome,
        memories_retrieved=retrieved,
        memories_written=list(memories_written or []),
        injected=bool(retrieved),
        previous_outcome=previous_outcome,
    )
