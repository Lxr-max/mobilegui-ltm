"""MemoryStore: write / retrieve / inject / export / import / namespace.

This is the SDK surface. It is not an agent and not a generic chat-memory OS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from mobilegui_ltm.encode.traj_summarizer import TrajectorySummarizer
from mobilegui_ltm.inject import PromptInjector
from mobilegui_ltm.plugins import Backend, Encoder, Injector, Retriever
from mobilegui_ltm.retrieve.keyword import BM25Retriever
from mobilegui_ltm.schema import (
    AttemptOutcome,
    EvalTask,
    InjectionTarget,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    SessionBundle,
    Trajectory,
    coerce_outcome,
    coerce_trajectory,
)
from mobilegui_ltm.store.json import JsonFileBackend

__all__ = ["MemoryStore", "create_store"]


class MemoryStore:
    """Namespaced long-term memory for a mobile GUI agent.

    Parameters
    ----------
    backend, encoder, retriever, injector:
        Plugin points. Defaults are JSON files, heuristic summarizer, BM25,
        and a prompt injector.
    agent_id:
        Namespace used for multi-agent isolation (``namespace()`` / ``clear()``).
    enabled:
        When False, retrieve/write/inject become no-ops (LTM-off ablation).
    """

    def __init__(
        self,
        backend: Backend,
        encoder: Encoder | None = None,
        retriever: Retriever | None = None,
        injector: Injector | None = None,
        *,
        agent_id: str = "default",
        enabled: bool = True,
    ) -> None:
        self.backend = backend
        self.encoder = encoder or TrajectorySummarizer()
        self.retriever = retriever or BM25Retriever()
        self.injector = injector or PromptInjector()
        self.agent_id = agent_id
        self.enabled = enabled

    # --- core contract -------------------------------------------------

    def write_attempt(
        self,
        task_id: str,
        attempt_k: int,
        traj: Trajectory | dict[str, Any] | list[Any] | str | None,
        outcome: AttemptOutcome | OutcomeStatus | dict[str, Any] | str | bool | None,
    ) -> list[MemoryRecord]:
        """Encode and persist one attempt. Writes both successes and failures."""
        if not self.enabled:
            return []
        trajectory = coerce_trajectory(traj)
        result = coerce_outcome(outcome)
        records = self.encoder.encode(
            task_id, attempt_k, trajectory, result, self.agent_id
        )
        stamped: list[MemoryRecord] = []
        for record in records:
            if record.agent_id != self.agent_id:
                record = record.model_copy(update={"agent_id": self.agent_id})
            stamped.append(record)
        if stamped:
            self.backend.upsert(stamped)
        return stamped

    def retrieve(
        self,
        query: str,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
        k: int = 5,
        *,
        kinds: Sequence[MemoryKind] | None = None,
    ) -> list[MemoryRecord]:
        """Return top-k memories for this namespace (never resets across attempts)."""
        if not self.enabled:
            return []
        candidates = self.backend.list(agent_id=self.agent_id, kinds=kinds)
        return self.retriever.retrieve(
            candidates,
            query,
            task_id=task_id,
            app_ids=app_ids,
            k=k,
        )

    def inject(
        self,
        prompt_or_state: Any,
        memories: Sequence[MemoryRecord],
        *,
        target: InjectionTarget | str = InjectionTarget.SYSTEM,
    ) -> Any:
        """Fold memories into a prompt or planner/worker state dict."""
        if not self.enabled or not memories:
            return prompt_or_state
        return self.injector.inject(prompt_or_state, memories, target=target)

    def export_session(self, path: str | Path | None = None) -> SessionBundle:
        """Export this namespace for cross-machine reproduction."""
        bundle = SessionBundle(
            agent_id=self.agent_id,
            memories=self.backend.list(agent_id=self.agent_id),
        )
        if path is not None:
            dest = Path(path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        return bundle

    def import_session(
        self,
        session: SessionBundle | dict[str, Any] | str | Path,
        *,
        into_current_namespace: bool = True,
    ) -> int:
        """Import a previously exported session. Returns the number of records."""
        bundle = _load_bundle(session)
        records = list(bundle.memories)
        if into_current_namespace:
            records = [
                r.model_copy(update={"agent_id": self.agent_id}) for r in records
            ]
        if records:
            self.backend.upsert(records)
        return len(records)

    def clear(self, *, all_agents: bool = False) -> int:
        """Delete this namespace, or the entire backend if ``all_agents``."""
        if all_agents:
            return self.backend.delete()
        return self.backend.delete(agent_id=self.agent_id)

    def namespace(self, agent_id: str) -> "MemoryStore":
        """Return a store bound to another agent id (same backend/plugins)."""
        return MemoryStore(
            backend=self.backend,
            encoder=self.encoder,
            retriever=self.retriever,
            injector=self.injector,
            agent_id=agent_id,
            enabled=self.enabled,
        )

    def list_memories(
        self,
        *,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
        kinds: Sequence[MemoryKind] | None = None,
    ) -> list[MemoryRecord]:
        """Unranked listing for this namespace (debugging / tests)."""
        return self.backend.list(
            agent_id=self.agent_id,
            task_id=task_id,
            app_ids=app_ids,
            kinds=kinds,
        )


def create_store(
    path: str | Path | None = None,
    *,
    agent_id: str = "default",
    enabled: bool = True,
    backend: Backend | None = None,
    encoder: Encoder | None = None,
    retriever: Retriever | None = None,
    injector: Injector | None = None,
) -> MemoryStore:
    """Factory: JSON-file store under ``path`` (default ``./.mobilegui_ltm``)."""
    if backend is None:
        root = Path(path) if path is not None else Path.cwd() / ".mobilegui_ltm"
        backend = JsonFileBackend(root)
    return MemoryStore(
        backend=backend,
        encoder=encoder,
        retriever=retriever,
        injector=injector,
        agent_id=agent_id,
        enabled=enabled,
    )


def memories_for_task(
    store: MemoryStore,
    task: EvalTask,
    *,
    k: int = 8,
) -> list[MemoryRecord]:
    """Convenience retrieve using an ``EvalTask``."""
    return store.retrieve(
        task.retrieval_query(),
        task_id=task.task_id,
        app_ids=task.app_ids or None,
        k=k,
    )


def _load_bundle(session: SessionBundle | dict[str, Any] | str | Path) -> SessionBundle:
    if isinstance(session, SessionBundle):
        return session
    if isinstance(session, dict):
        return SessionBundle.model_validate(session)
    path = Path(session)
    return SessionBundle.model_validate_json(path.read_text(encoding="utf-8"))
