"""Reference adapter for planner / worker (Agent-S2-style) mobile GUI agents.

Pluggability proof: the same ``MemoryStore`` can inject failure notes + subgoal
traces into the planner and UI facts into the worker without forking the agent.
"""

from __future__ import annotations

from typing import Any, Sequence

from mobilegui_ltm.api import MemoryStore
from mobilegui_ltm.schema import InjectionTarget, MemoryKind, MemoryRecord


class PlannerWorkerAdapter:
    """Split retrieve + inject across planner and worker prompts."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        planner_k: int = 6,
        worker_k: int = 4,
    ) -> None:
        self.store = store
        self.planner_k = planner_k
        self.worker_k = worker_k

    def memories_for_planner(
        self,
        query: str,
        *,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
    ) -> list[MemoryRecord]:
        records = self.store.retrieve(
            query, task_id=task_id, app_ids=app_ids, k=self.planner_k * 2
        )
        preferred = (
            MemoryKind.FAILURE_NOTE,
            MemoryKind.SUBGOAL_TRACE,
            MemoryKind.UI_FACT,
        )
        ordered = [r for kind in preferred for r in records if r.kind == kind]
        leftover = [r for r in records if r not in ordered]
        return (ordered + leftover)[: self.planner_k]

    def memories_for_worker(
        self,
        query: str,
        *,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
    ) -> list[MemoryRecord]:
        records = self.store.retrieve(
            query,
            task_id=task_id,
            app_ids=app_ids,
            k=self.worker_k * 2,
            kinds=(MemoryKind.UI_FACT, MemoryKind.SHORTCUT, MemoryKind.FAILURE_NOTE),
        )
        ui_first = [r for r in records if r.kind == MemoryKind.UI_FACT]
        rest = [r for r in records if r not in ui_first]
        return (ui_first + rest)[: self.worker_k]

    def inject_planner(
        self,
        prompt_or_state: Any,
        query: str,
        *,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
    ) -> Any:
        memories = self.memories_for_planner(query, task_id=task_id, app_ids=app_ids)
        return self.store.inject(
            prompt_or_state, memories, target=InjectionTarget.PLANNER
        )

    def inject_worker(
        self,
        prompt_or_state: Any,
        query: str,
        *,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
    ) -> Any:
        memories = self.memories_for_worker(query, task_id=task_id, app_ids=app_ids)
        return self.store.inject(
            prompt_or_state, memories, target=InjectionTarget.WORKER
        )
