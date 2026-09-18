"""Online episode diagnostics and optional EpisodeReflector plugin."""

from mobilegui_ltm.diagnostics.auditor import AuditReport, EvalAuditor
from mobilegui_ltm.diagnostics.attribution import aligned, attribute_episode, attribute_record
from mobilegui_ltm.diagnostics.failure_taxonomy import classify_failure
from mobilegui_ltm.diagnostics.online import OnlineDiagnostics, resolve_diagnostics, trace_from_write
from mobilegui_ltm.diagnostics.outcome import (
    CallableOutcomeProvider,
    NullOutcomeProvider,
    OutcomeProvider,
)
from mobilegui_ltm.diagnostics.reflector import EpisodeReflector, NullEpisodeReflector
from mobilegui_ltm.diagnostics.schema import (
    Attribution,
    DiagnosticEvent,
    DiagnosticReport,
    EpisodeTrace,
    FailureClass,
    MemoryAction,
    MemoryActionType,
    is_quarantined,
)

__all__ = [
    "Attribution",
    "AuditReport",
    "CallableOutcomeProvider",
    "DiagnosticEvent",
    "DiagnosticReport",
    "EpisodeReflector",
    "EpisodeTrace",
    "EvalAuditor",
    "FailureClass",
    "MemoryAction",
    "MemoryActionType",
    "NullEpisodeReflector",
    "NullOutcomeProvider",
    "OnlineDiagnostics",
    "OutcomeProvider",
    "aligned",
    "attribute_episode",
    "attribute_record",
    "classify_failure",
    "is_quarantined",
    "resolve_diagnostics",
    "trace_from_write",
]
