"""MemoryStore: write / retrieve / inject / export / import / namespace.

This is the SDK surface. It is not an agent and not a generic chat-memory OS.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Sequence

from mobilegui_ltm.encode.traj_summarizer import TrajectorySummarizer
from mobilegui_ltm.inject import PromptInjector
from mobilegui_ltm.plugins import Backend, Embedder, Encoder, Injector, Retriever
from mobilegui_ltm.retrieve.embed import EmbeddingRetriever, HybridRetriever
from mobilegui_ltm.retrieve.embedder import HashingEmbedder, resolve_embedder
from mobilegui_ltm.retrieve.index import VectorSidecar
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
        Plugin points. Defaults are JSON files, heuristic summarizer (with
        shortcuts), BM25, and a prompt injector.
    embedder:
        Optional vector encoder. When set, ``write_attempt`` persists embeddings
        on each record and a ``{agent}.vectors.json`` sidecar is kept in sync.
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
        embedder: Embedder | None = None,
        agent_id: str = "default",
        enabled: bool = True,
    ) -> None:
        self.backend = backend
        self.encoder = encoder or TrajectorySummarizer()
        self.retriever = retriever or BM25Retriever()
        self.injector = injector or PromptInjector()
        if embedder is None:
            embedder = getattr(self.retriever, "embedder", None)
        self.embedder = embedder
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
        stamped = self._ensure_embeddings(stamped)
        if stamped:
            self.backend.upsert(stamped)
            self._sync_sidecar()
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
        candidates = self._hydrate_vectors(candidates)
        missing = [c for c in candidates if self.embedder and not c.embedding]
        if missing:
            filled = self._ensure_embeddings(missing, force=True)
            self.backend.upsert(filled)
            by_id = {item.id: item for item in filled}
            candidates = [by_id.get(c.id, c) for c in candidates]
            self._sync_sidecar()
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
        records = self._ensure_embeddings(records)
        if records:
            self.backend.upsert(records)
            self._sync_sidecar()
        return len(records)

    def clear(self, *, all_agents: bool = False) -> int:
        """Delete this namespace, or the entire backend if ``all_agents``."""
        sidecar = self._sidecar()
        if all_agents:
            removed = self.backend.delete()
            if sidecar is not None:
                sidecar.delete()
            return removed
        removed = self.backend.delete(agent_id=self.agent_id)
        if sidecar is not None:
            sidecar.delete(self.agent_id)
        return removed

    def namespace(self, agent_id: str) -> "MemoryStore":
        """Return a store bound to another agent id (same backend/plugins)."""
        return MemoryStore(
            backend=self.backend,
            encoder=self.encoder,
            retriever=self.retriever,
            injector=self.injector,
            embedder=self.embedder,
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

    def rebuild_index(self, *, all_agents: bool = False) -> int:
        """Re-embed persisted memories with the current embedder.

        Use after switching embedders (hashing → sentence-transformers) or if
        the sidecar / ``MemoryRecord.embedding`` field is stale. Embeddings are
        written back onto JSON records **and** ``{agent}.vectors.json``.
        """
        if self.embedder is None:
            raise RuntimeError(
                "No embedder configured. Pass embedder='hashing' or retriever='hybrid' "
                "to create_store()."
            )
        agent_id = None if all_agents else self.agent_id
        records = self.backend.list(agent_id=agent_id)
        stamped = self._ensure_embeddings(records, force=True)
        if stamped:
            self.backend.upsert(stamped)
            if all_agents:
                by_agent: dict[str, list[MemoryRecord]] = {}
                for record in stamped:
                    by_agent.setdefault(record.agent_id, []).append(record)
                sidecar = self._sidecar()
                if sidecar is not None:
                    for aid, batch in by_agent.items():
                        sidecar.write(
                            aid,
                            batch,
                            embedder_name=getattr(self.embedder, "name", "embedder"),
                            dim=int(getattr(self.embedder, "dim", 0) or 0),
                        )
            else:
                self._sync_sidecar()
        return len(stamped)

    def _ensure_embeddings(
        self,
        records: Sequence[MemoryRecord],
        *,
        force: bool = False,
    ) -> list[MemoryRecord]:
        if not self.embedder:
            return list(records)
        pending_idx: list[int] = []
        texts: list[str] = []
        out = list(records)
        for i, record in enumerate(out):
            if force or not record.embedding:
                pending_idx.append(i)
                texts.append(record.searchable_text())
        if not texts:
            return out
        vectors = self.embedder.embed(texts)
        name = getattr(self.embedder, "name", "embedder")
        for i, vec in zip(pending_idx, vectors, strict=True):
            meta = dict(out[i].metadata)
            meta["embedder"] = name
            out[i] = out[i].model_copy(update={"embedding": list(vec), "metadata": meta})
        return out

    def _hydrate_vectors(self, records: list[MemoryRecord]) -> list[MemoryRecord]:
        sidecar = self._sidecar()
        if sidecar is None:
            return records
        return sidecar.apply(self.agent_id, records)

    def _sync_sidecar(self) -> None:
        sidecar = self._sidecar()
        if sidecar is None or self.embedder is None:
            return
        records = self.backend.list(agent_id=self.agent_id)
        sidecar.write(
            self.agent_id,
            records,
            embedder_name=getattr(self.embedder, "name", "embedder"),
            dim=int(getattr(self.embedder, "dim", 0) or 0),
        )

    def _sidecar(self) -> VectorSidecar | None:
        root = getattr(self.backend, "root", None)
        if root is None:
            return None
        return VectorSidecar(root)


def create_store(
    path: str | Path | None = None,
    *,
    agent_id: str = "default",
    enabled: bool = True,
    backend: Backend | None = None,
    encoder: Encoder | None = None,
    retriever: Retriever | str | None = None,
    injector: Injector | None = None,
    embedder: Embedder | str | Callable[[str], list[float]] | None = None,
    lexical_weight: float = 0.5,
    vector_weight: float = 0.5,
) -> MemoryStore:
    """Factory: JSON-file store under ``path`` (default ``./.mobilegui_ltm``).

    Retrieval modes::

        create_store(path)                           # BM25 (default)
        create_store(path, retriever="hybrid")       # BM25 + hashing vectors
        create_store(path, embedder="hashing")       # same as hybrid
        create_store(path, retriever="vector", embedder=FakeEmbedder())
        create_store(path, embedder="sentence-transformers")  # needs [embed]
    """
    resolved_embedder = resolve_embedder(embedder)
    resolved_retriever = _resolve_retriever(
        retriever,
        resolved_embedder,
        lexical_weight=lexical_weight,
        vector_weight=vector_weight,
    )
    if resolved_embedder is None:
        resolved_embedder = getattr(resolved_retriever, "embedder", None)
    if backend is None:
        root = Path(path) if path is not None else Path.cwd() / ".mobilegui_ltm"
        backend = JsonFileBackend(root)
    return MemoryStore(
        backend=backend,
        encoder=encoder,
        retriever=resolved_retriever,
        injector=injector,
        embedder=resolved_embedder,
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


def _resolve_retriever(
    retriever: Retriever | str | None,
    embedder: Embedder | None,
    *,
    lexical_weight: float,
    vector_weight: float,
) -> Retriever:
    if retriever is None:
        if embedder is not None:
            return HybridRetriever(
                embedder,
                lexical_weight=lexical_weight,
                vector_weight=vector_weight,
            )
        return BM25Retriever()
    if isinstance(retriever, str):
        key = retriever.strip().lower()
        if key in {"bm25", "keyword"}:
            return BM25Retriever()
        if key == "hybrid":
            return HybridRetriever(
                embedder or HashingEmbedder(),
                lexical_weight=lexical_weight,
                vector_weight=vector_weight,
            )
        if key in {"vector", "embed", "embedding"}:
            return EmbeddingRetriever(embedder or HashingEmbedder())
        raise ValueError(
            f"Unknown retriever {retriever!r}. Use 'bm25', 'hybrid', or 'vector'."
        )
    return retriever


def _load_bundle(session: SessionBundle | dict[str, Any] | str | Path) -> SessionBundle:
    if isinstance(session, SessionBundle):
        return session
    if isinstance(session, dict):
        return SessionBundle.model_validate(session)
    path = Path(session)
    return SessionBundle.model_validate_json(path.read_text(encoding="utf-8"))
