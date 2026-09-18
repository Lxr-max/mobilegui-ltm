"""Plugin protocols: Backend, Encoder, Retriever, Injector (plus optional Embedder).

Optional diagnostics plugins (OutcomeProvider, EpisodeReflector) live in
``mobilegui_ltm.diagnostics``. Swap any of these without forking the agent.
See ``docs/design.md``.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from mobilegui_ltm.schema import (
    AttemptOutcome,
    InjectionTarget,
    MemoryKind,
    MemoryRecord,
    Trajectory,
)


class Embedder(Protocol):
    """Optional vector encoder used by hybrid / vector retrievers."""

    dim: int
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class Backend(Protocol):
    """Persistence for ``MemoryRecord`` objects."""

    def upsert(self, records: Sequence[MemoryRecord]) -> None: ...

    def list(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
        kinds: Sequence[MemoryKind] | None = None,
    ) -> list[MemoryRecord]: ...

    def delete(self, *, agent_id: str | None = None) -> int: ...

    def replace_all(self, records: Sequence[MemoryRecord]) -> None: ...


class Encoder(Protocol):
    """Turn a trajectory + outcome into typed memory records."""

    def encode(
        self,
        task_id: str,
        attempt_k: int,
        traj: Trajectory,
        outcome: AttemptOutcome,
        agent_id: str,
    ) -> list[MemoryRecord]: ...


class Retriever(Protocol):
    """Rank a candidate list for a query."""

    def retrieve(
        self,
        records: Sequence[MemoryRecord],
        query: str,
        *,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
        k: int = 5,
        kind_weights: dict | None = None,
        state: Any = None,
        **kwargs: Any,
    ) -> list[MemoryRecord]: ...


class Injector(Protocol):
    """Fold memories into a prompt or planner/worker state."""

    def inject(
        self,
        prompt_or_state: Any,
        memories: Sequence[MemoryRecord],
        *,
        target: InjectionTarget | str = InjectionTarget.SYSTEM,
        block: str | None = None,
        split_blocks: bool = False,
    ) -> Any: ...
