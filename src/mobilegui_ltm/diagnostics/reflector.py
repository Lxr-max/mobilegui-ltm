"""Optional EpisodeReflector plugin (tip / shortcut rewrite proposals).

Default ``NullEpisodeReflector`` returns no proposals. A later LLM client can
implement this protocol; proposals are **not** applied unless the caller
passes them to ``OnlineDiagnostics.apply(..., include_reflector=True)``.
Tests and CI never require an LLM.
"""

from __future__ import annotations

from typing import Protocol

from mobilegui_ltm.diagnostics.schema import DiagnosticReport, EpisodeTrace, MemoryAction


class EpisodeReflector(Protocol):
    """Hook that may propose tip/shortcut rewrites after an episode."""

    def reflect(
        self,
        trace: EpisodeTrace,
        report: DiagnosticReport,
    ) -> list[MemoryAction]: ...


class NullEpisodeReflector:
    """Default plugin: no rewrite proposals, no network, no API keys."""

    def reflect(
        self,
        trace: EpisodeTrace,
        report: DiagnosticReport,
    ) -> list[MemoryAction]:
        return []
