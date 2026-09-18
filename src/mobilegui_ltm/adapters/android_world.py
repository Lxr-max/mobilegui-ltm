"""Thin AndroidWorld / MobileWorld-style adapter (MVP stub).

This module does **not** import AndroidWorld or MobileWorld. It documents the
hook points a harness should call so the UI/environment can reset between
attempts while long-term memory does not.

Typical wiring::

    from mobilegui_ltm import create_store
    from mobilegui_ltm.adapters import AndroidWorldAdapter

    store = create_store(path="runs/exp1", agent_id="agent-a")
    adapter = AndroidWorldAdapter(store)

    env.reset()  # emulator / activity reset is allowed
    for attempt_k in range(1, max_attempts + 1):
        prompt = adapter.before_attempt(task_id, goal, system_prompt, app_ids=[app])
        traj, info = agent.act(env, prompt)
        adapter.after_attempt(task_id, attempt_k, traj, outcome=info)
"""

from __future__ import annotations

from typing import Any, Sequence

from mobilegui_ltm.api import MemoryStore
from mobilegui_ltm.schema import InjectionTarget, MemoryRecord


class AndroidWorldAdapter:
    """Persist retrieve → inject → write around one environment attempt."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        retrieve_k: int = 8,
        target: InjectionTarget | str = InjectionTarget.SYSTEM,
    ) -> None:
        self.store = store
        self.retrieve_k = retrieve_k
        self.target = target

    def before_attempt(
        self,
        task_id: str,
        query: str,
        prompt_or_state: Any,
        *,
        app_ids: Sequence[str] | None = None,
    ) -> Any:
        memories = self.store.retrieve(
            query, task_id=task_id, app_ids=app_ids, k=self.retrieve_k
        )
        return self.store.inject(prompt_or_state, memories, target=self.target)

    def after_attempt(
        self,
        task_id: str,
        attempt_k: int,
        traj: Any,
        outcome: Any,
    ) -> list[MemoryRecord]:
        return self.store.write_attempt(task_id, attempt_k, traj, outcome)

    def register_app_prior(
        self,
        app_id: str,
        *,
        name: str = "",
        summary: str = "",
        capabilities: Sequence[str] | None = None,
        package: str | None = None,
        extras: dict[str, Any] | None = None,
    ) -> Any:
        """Record an installed-app catalog row. Does not call ADB or a device."""
        return self.store.register_app_prior(
            app_id,
            name=name,
            summary=summary,
            capabilities=capabilities,
            package=package,
            extras=extras,
        )
