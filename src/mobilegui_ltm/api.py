"""MemoryStore: write / retrieve / inject / export / import / namespace.

This is the SDK surface. It is not an agent and not a generic chat-memory OS.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Sequence

from mobilegui_ltm.blocks import (
    MemoryBlock,
    block_for_kind,
    filter_blocks,
    normalize_block_names,
    resolve_block_registry,
)
from mobilegui_ltm.encode.traj_summarizer import TrajectorySummarizer
from mobilegui_ltm.inject import PromptInjector, format_shortcut_for_worker
from mobilegui_ltm.integrity import IntegrityGuard, resolve_integrity, tamper
from mobilegui_ltm.localrag import (
    CatalogLocalRAG,
    LocalRAG,
    NullLocalRAG,
    facts_to_records,
    resolve_local_rag,
)
from mobilegui_ltm.plugins import Backend, Embedder, Encoder, Injector, Retriever
from mobilegui_ltm.profiles import MemoryProfile, resolve_profile
from mobilegui_ltm.retrieve.embed import EmbeddingRetriever, HybridRetriever
from mobilegui_ltm.retrieve.embedder import HashingEmbedder, resolve_embedder
from mobilegui_ltm.retrieve.index import VectorSidecar
from mobilegui_ltm.retrieve.keyword import BM25Retriever
from mobilegui_ltm.retrieve.scoring import (
    active_only,
    apply_hard_filters,
    apply_stability_filter,
    expand_links,
    normalize_kind_weights,
)
from mobilegui_ltm.schema import (
    PROMOTABLE_KINDS,
    AttemptOutcome,
    EvalTask,
    InjectionTarget,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    RecordStatus,
    Stability,
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
        write_kinds: Sequence[MemoryKind] | None = None,
        retrieve_kinds: Sequence[MemoryKind] | None = None,
        kind_weights: dict[str, float] | dict[MemoryKind, float] | None = None,
        expand_hops: int = 0,
        strict_task: bool = False,
        blocks: dict[str, MemoryBlock | Sequence[MemoryKind | str]] | Sequence[MemoryBlock] | None = None,
        local_rag: LocalRAG | bool | Sequence[Any] | None = None,
        blend_local_rag: bool = False,
        local_rag_k: int = 3,
        include_candidates: bool = True,
        include_retired: bool = False,
        prefer_stable: bool = True,
        promote_after: int = 2,
        demote_after: int = 2,
        promote_kinds: Sequence[MemoryKind] | None = None,
        integrity: IntegrityGuard | bool | None = None,
        hmac_key: str | bytes | None = None,
        verify_on_retrieve: bool = True,
        drop_unverified: bool = True,
    ) -> None:
        self.backend = backend
        self.encoder = encoder or TrajectorySummarizer()
        self.retriever = retriever or BM25Retriever(kind_weights=kind_weights)
        self.injector = injector or PromptInjector()
        if embedder is None:
            embedder = getattr(self.retriever, "embedder", None)
        self.embedder = embedder
        self.agent_id = agent_id
        self.enabled = enabled
        self.write_kinds = tuple(write_kinds) if write_kinds is not None else None
        self.retrieve_kinds = tuple(retrieve_kinds) if retrieve_kinds is not None else None
        self.kind_weights = normalize_kind_weights(kind_weights)
        self.expand_hops = int(expand_hops)
        self.strict_task = strict_task
        self.blocks = resolve_block_registry(blocks)
        self.local_rag: LocalRAG = resolve_local_rag(local_rag)
        self.blend_local_rag = bool(blend_local_rag)
        self.local_rag_k = int(local_rag_k)
        self.include_candidates = include_candidates
        self.include_retired = include_retired
        self.prefer_stable = prefer_stable
        self.promote_after = max(1, int(promote_after))
        self.demote_after = max(1, int(demote_after))
        self.promote_kinds = tuple(promote_kinds) if promote_kinds is not None else tuple(PROMOTABLE_KINDS)
        self.integrity = resolve_integrity(
            integrity,
            hmac_key=hmac_key,
            verify_on_retrieve=verify_on_retrieve,
            drop_unverified=drop_unverified,
        )

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
        written = self._commit(records)
        self._apply_promotion_gate(result, task_id=task_id, app_ids=trajectory.app_ids)
        return written

    def remember(
        self,
        content: str,
        *,
        kind: MemoryKind | str = MemoryKind.UI_FACT,
        key: str | None = None,
        task_id: str = "",
        attempt_k: int = 0,
        app_ids: Sequence[str] | None = None,
        screen: str | None = None,
        tags: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
        depends_on: Sequence[str] | None = None,
        supersede: bool = True,
        block: str | None = None,
        stability: Stability | str | None = None,
    ) -> MemoryRecord | None:
        """Insert a fact mid-episode. Same ``key`` supersedes the previous active value."""
        if not self.enabled:
            return None
        kwargs: dict[str, Any] = {}
        if block is not None:
            kwargs["block"] = block
        if stability is not None:
            kwargs["stability"] = Stability(stability)
        record = MemoryRecord(
            kind=MemoryKind(kind),
            content=content,
            task_id=task_id,
            attempt_k=attempt_k,
            agent_id=self.agent_id,
            app_ids=list(app_ids or []),
            tags=list(tags or []),
            metadata=dict(metadata or {}),
            logical_key=key,
            screen=screen,
            depends_on=list(depends_on or []),
            **kwargs,
        )
        written = self._commit(
            [record], supersede=supersede, respect_write_kinds=False
        )
        return written[0] if written else None

    def update(
        self,
        key_or_id: str,
        *,
        content: str | None = None,
        screen: str | None = None,
        tags: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
        depends_on: Sequence[str] | None = None,
        block: str | None = None,
        stability: Stability | str | None = None,
    ) -> MemoryRecord | None:
        """In-place update of an active record (by id or logical key)."""
        if not self.enabled:
            return None
        current = self.get(key_or_id, include_inactive=False)
        if current is None:
            return None
        patch: dict[str, Any] = {}
        if content is not None:
            patch["content"] = content
        if screen is not None:
            patch["screen"] = screen
        if tags is not None:
            patch["tags"] = list(tags)
        if metadata is not None:
            merged = dict(current.metadata)
            merged.update(metadata)
            patch["metadata"] = merged
        if depends_on is not None:
            patch["depends_on"] = list(depends_on)
        if block is not None:
            patch["block"] = block
        if stability is not None:
            patch["stability"] = Stability(stability)
        updated = current.model_copy(update=patch)
        stamped = self._ensure_embeddings([updated], force=True)
        if self.integrity:
            stamped = self.integrity.stamp_many(stamped)
        self.backend.upsert(stamped)
        self._sync_sidecar()
        return stamped[0]

    def delete(self, key_or_id: str, *, hard: bool = False) -> int:
        """Soft-delete (default) or hard-delete a record by id or logical key."""
        if not self.enabled:
            return 0
        current = self.get(key_or_id, include_inactive=True)
        if current is None:
            return 0
        if hard:
            remover = getattr(self.backend, "remove", None)
            if callable(remover):
                n = int(remover([current.id], agent_id=self.agent_id))
            else:
                n = 1 if self._soft_delete(current) else 0
            self._sync_sidecar()
            return n
        return 1 if self._soft_delete(current) else 0

    def get(
        self,
        key_or_id: str,
        *,
        include_inactive: bool = False,
    ) -> MemoryRecord | None:
        records = self.backend.list(agent_id=self.agent_id)
        if not include_inactive:
            records = active_only(records)
        by_id = None
        by_key = None
        for record in records:
            if record.id == key_or_id:
                by_id = record
                break
            if record.logical_key == key_or_id and by_key is None:
                by_key = record
        return by_id or by_key

    def retrieve(
        self,
        query: str,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
        k: int = 5,
        *,
        kinds: Sequence[MemoryKind] | None = None,
        screen: str | None = None,
        strict_task: bool | None = None,
        state: Any = None,
        kind_weights: dict[str, float] | dict[MemoryKind, float] | None = None,
        expand_hops: int | None = None,
        block: str | None = None,
        blocks: Sequence[str] | None = None,
        include_candidates: bool | None = None,
        prefer_stable: bool | None = None,
        blend_local_rag: bool | None = None,
    ) -> list[MemoryRecord]:
        """Return top-k memories for this namespace (never resets across attempts)."""
        if not self.enabled:
            return []
        kind_filter = kinds if kinds is not None else self.retrieve_kinds
        pool = self.backend.list(agent_id=self.agent_id, kinds=kind_filter)
        pool = active_only(self._hydrate_vectors(pool))
        names = normalize_block_names(block, blocks)
        if names:
            pool = filter_blocks(pool, names, registry=self.blocks)
        pool = apply_stability_filter(
            pool,
            include_candidates=self.include_candidates
            if include_candidates is None
            else include_candidates,
            include_retired=self.include_retired,
        )
        if self.integrity and self.integrity.verify_on_retrieve:
            ok, bad = self.integrity.check(pool)
            pool = ok if self.integrity.drop_unverified else ok + bad
        missing = [c for c in pool if self.embedder and not c.embedding]
        if missing:
            filled = self._ensure_embeddings(missing, force=True)
            if self.integrity:
                filled = self.integrity.stamp_many(filled)
            self.backend.upsert(filled)
            by_id = {item.id: item for item in filled}
            pool = [by_id.get(c.id, c) for c in pool]
            self._sync_sidecar()
        candidates = apply_hard_filters(
            pool,
            app_ids=app_ids,
            screen=screen,
            task_id=task_id,
            strict_task=self.strict_task if strict_task is None else strict_task,
        )
        prefer = self.prefer_stable if prefer_stable is None else prefer_stable
        ranked = self.retriever.retrieve(
            candidates,
            query,
            task_id=task_id,
            app_ids=None,
            k=k,
            kind_weights=kind_weights or self.kind_weights,
            state=state,
            prefer_stable=prefer,
        )
        hops = self.expand_hops if expand_hops is None else expand_hops
        if hops:
            ranked = expand_links(ranked, pool, hops=hops)
        do_blend = self.blend_local_rag if blend_local_rag is None else blend_local_rag
        if do_blend and getattr(self.local_rag, "enabled", False):
            if not names or "app_priors" in names:
                facts = self.local_rag.lookup(
                    query, app_ids=app_ids, k=self.local_rag_k
                )
                extra = facts_to_records(
                    facts, agent_id=self.agent_id, task_id=task_id or ""
                )
                seen = {item.logical_key or item.id for item in ranked}
                for item in extra:
                    key = item.logical_key or item.id
                    if key not in seen:
                        ranked.append(item)
                        seen.add(key)
        return ranked

    def inject(
        self,
        prompt_or_state: Any,
        memories: Sequence[MemoryRecord],
        *,
        target: InjectionTarget | str = InjectionTarget.SYSTEM,
        block: str | None = None,
        blocks: Sequence[str] | None = None,
        split_blocks: bool | None = None,
    ) -> Any:
        """Fold memories into a prompt or planner/worker state dict.

        Pass ``block`` / ``blocks`` to inject one named memory view instead of
        dumping every retrieved kind into a single undifferentiated blob.
        """
        if not self.enabled or not memories:
            return prompt_or_state
        names = normalize_block_names(block, blocks)
        mems = list(memories)
        if names:
            mems = filter_blocks(mems, names, registry=self.blocks)
            if not mems:
                return prompt_or_state
        one = names[0] if len(names) == 1 else None
        split = (len(names) > 1) if split_blocks is None else split_blocks
        return self.injector.inject(
            prompt_or_state,
            mems,
            target=target,
            block=one,
            split_blocks=split,
        )

    def inject_block(
        self,
        prompt_or_state: Any,
        block: str,
        query: str,
        *,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
        k: int = 8,
        target: InjectionTarget | str = InjectionTarget.SYSTEM,
        screen: str | None = None,
        state: Any = None,
    ) -> Any:
        """Retrieve one named block and inject it."""
        memories = self.retrieve(
            query,
            task_id=task_id,
            app_ids=app_ids,
            k=k,
            block=block,
            screen=screen,
            state=state,
        )
        dest = target
        return self.inject(prompt_or_state, memories, target=dest, block=block)

    def promote(self, key_or_id: str) -> MemoryRecord | None:
        """Mark a candidate shortcut/anchor as stable."""
        if not self.enabled:
            return None
        current = self.get(key_or_id, include_inactive=False)
        if current is None:
            return None
        updated = current.model_copy(
            update={"stability": Stability.STABLE, "fail_count": 0}
        )
        return self._persist_records([updated])[0]

    def demote(self, key_or_id: str, *, retire: bool = False) -> MemoryRecord | None:
        """Demote stable → candidate, or candidate → retired."""
        if not self.enabled:
            return None
        current = self.get(key_or_id, include_inactive=False)
        if current is None:
            return None
        if retire or current.stability is Stability.CANDIDATE:
            new_state = Stability.RETIRED
        else:
            new_state = Stability.CANDIDATE
        updated = current.model_copy(update={"stability": new_state})
        return self._persist_records([updated])[0]

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
        """Register an installed-app fact on the LocalRAG catalog (no ADB)."""
        rag = self.local_rag
        if rag is None or isinstance(rag, NullLocalRAG) or not getattr(rag, "enabled", False):
            rag = CatalogLocalRAG()
            self.local_rag = rag
        return rag.register_app(
            app_id,
            name=name,
            summary=summary,
            capabilities=capabilities,
            package=package,
            extras=extras,
        )

    def poison(
        self,
        key_or_id: str,
        *,
        content: str | None = None,
    ) -> MemoryRecord | None:
        """Research fixture: mutate content without refreshing ``content_hash``."""
        current = self.get(key_or_id, include_inactive=True)
        if current is None:
            return None
        poisoned = tamper(current, content=content)
        self.backend.upsert([poisoned])
        return poisoned

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
        if self.integrity:
            records = self.integrity.stamp_many(records)
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
            write_kinds=self.write_kinds,
            retrieve_kinds=self.retrieve_kinds,
            kind_weights=self.kind_weights,
            expand_hops=self.expand_hops,
            strict_task=self.strict_task,
            blocks=self.blocks,
            local_rag=self.local_rag,
            blend_local_rag=self.blend_local_rag,
            local_rag_k=self.local_rag_k,
            include_candidates=self.include_candidates,
            include_retired=self.include_retired,
            prefer_stable=self.prefer_stable,
            promote_after=self.promote_after,
            demote_after=self.demote_after,
            promote_kinds=self.promote_kinds,
            integrity=self.integrity,
        )

    def list_memories(
        self,
        *,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
        kinds: Sequence[MemoryKind] | None = None,
        include_inactive: bool = False,
        block: str | None = None,
        blocks: Sequence[str] | None = None,
    ) -> list[MemoryRecord]:
        """Unranked listing for this namespace (debugging / tests)."""
        records = self.backend.list(
            agent_id=self.agent_id,
            task_id=task_id,
            app_ids=app_ids,
            kinds=kinds,
        )
        names = normalize_block_names(block, blocks)
        if names:
            records = filter_blocks(records, names, registry=self.blocks)
        if include_inactive:
            return records
        return active_only(records)

    def format_worker_shortcuts(
        self,
        memories: Sequence[MemoryRecord] | None = None,
    ) -> str:
        """Callable-style shortcut block for a worker prompt."""
        records = (
            list(memories)
            if memories is not None
            else self.list_memories(kinds=[MemoryKind.SHORTCUT])
        )
        lines = [
            format_shortcut_for_worker(record)
            for record in records
            if record.kind is MemoryKind.SHORTCUT
        ]
        return "\n".join(lines)

    def _commit(
        self,
        records: Sequence[MemoryRecord],
        *,
        supersede: bool = True,
        respect_write_kinds: bool = True,
    ) -> list[MemoryRecord]:
        incoming = list(records)
        if respect_write_kinds and self.write_kinds is not None:
            allowed = set(self.write_kinds)
            incoming = [record for record in incoming if record.kind in allowed]
        if not incoming:
            return []
        incoming = [
            record.model_copy(update={"agent_id": self.agent_id}) for record in incoming
        ]
        incoming = self._inherit_promotion(incoming)
        incoming = [self._assign_defaults(record) for record in incoming]
        incoming = _collapse_logical_keys(incoming)
        if supersede:
            self._supersede_existing(incoming)
        stamped = self._ensure_embeddings(incoming)
        if self.integrity:
            stamped = self.integrity.stamp_many(stamped)
        self.backend.upsert(stamped)
        self._sync_sidecar()
        return stamped

    def _supersede_existing(self, incoming: Sequence[MemoryRecord]) -> None:
        keyed = [record for record in incoming if record.logical_key]
        if not keyed:
            return
        existing = active_only(self.backend.list(agent_id=self.agent_id))
        incoming_ids = {record.id for record in incoming}
        demoted: list[MemoryRecord] = []
        for record in keyed:
            for old in existing:
                if old.id in incoming_ids or old.id == record.id:
                    continue
                if old.kind is not record.kind:
                    continue
                if not old.logical_key or old.logical_key != record.logical_key:
                    continue
                demoted.append(
                    old.model_copy(
                        update={
                            "status": RecordStatus.SUPERSEDED,
                            "superseded_by": record.id,
                        }
                    )
                )
        if demoted:
            self.backend.upsert(demoted)

    def _assign_defaults(self, record: MemoryRecord) -> MemoryRecord:
        patch: dict[str, Any] = {}
        if not record.block:
            patch["block"] = block_for_kind(record.kind, self.blocks)
        if record.kind in self.promote_kinds and "stability" not in record.model_fields_set:
            patch["stability"] = Stability.CANDIDATE
        if not patch:
            return record
        return record.model_copy(update=patch)

    def _inherit_promotion(self, incoming: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        existing = active_only(self.backend.list(agent_id=self.agent_id))
        by_key = {
            (record.kind, record.logical_key): record
            for record in existing
            if record.logical_key
        }
        out: list[MemoryRecord] = []
        for record in incoming:
            if not record.logical_key:
                out.append(record)
                continue
            old = by_key.get((record.kind, record.logical_key))
            if old is None or old.kind not in self.promote_kinds:
                out.append(record)
                continue
            out.append(
                record.model_copy(
                    update={
                        "success_count": old.success_count,
                        "fail_count": old.fail_count,
                        "stability": old.stability,
                    }
                )
            )
        return out

    def _apply_promotion_gate(
        self,
        outcome: AttemptOutcome,
        *,
        task_id: str,
        app_ids: Sequence[str] | None = None,
    ) -> None:
        if outcome.status not in {OutcomeStatus.SUCCESS, OutcomeStatus.FAILURE}:
            return
        records = [
            record
            for record in active_only(self.backend.list(agent_id=self.agent_id))
            if record.kind in self.promote_kinds
        ]
        patched: list[MemoryRecord] = []
        for record in records:
            if record.task_id and record.task_id != task_id:
                continue
            if outcome.status is OutcomeStatus.SUCCESS:
                n = record.success_count + 1
                update: dict[str, Any] = {"success_count": n}
                if n >= self.promote_after:
                    update["stability"] = Stability.STABLE
                    update["fail_count"] = 0
            else:
                n = record.fail_count + 1
                update = {"fail_count": n}
                if n >= self.demote_after:
                    update["stability"] = (
                        Stability.CANDIDATE
                        if record.stability is Stability.STABLE
                        else Stability.RETIRED
                    )
            patched.append(record.model_copy(update=update))
        if patched:
            self._persist_records(patched)

    def _persist_records(self, records: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        stamped = list(records)
        if self.integrity:
            stamped = self.integrity.stamp_many(stamped)
        self.backend.upsert(stamped)
        self._sync_sidecar()
        return stamped

    def _soft_delete(self, record: MemoryRecord) -> bool:
        if record.status is RecordStatus.DELETED:
            return False
        updated = record.model_copy(update={"status": RecordStatus.DELETED})
        self.backend.upsert([updated])
        self._sync_sidecar()
        return True

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
    profile: str | MemoryProfile | None = None,
    write_kinds: Sequence[MemoryKind] | None = None,
    retrieve_kinds: Sequence[MemoryKind] | None = None,
    kind_weights: dict[str, float] | dict[MemoryKind, float] | None = None,
    expand_hops: int | None = None,
    strict_task: bool = False,
    blocks: dict[str, MemoryBlock | Sequence[MemoryKind | str]] | Sequence[MemoryBlock] | None = None,
    local_rag: LocalRAG | bool | Sequence[Any] | None = None,
    blend_local_rag: bool = False,
    local_rag_k: int = 3,
    include_candidates: bool = True,
    include_retired: bool = False,
    prefer_stable: bool = True,
    promote_after: int = 2,
    demote_after: int = 2,
    promote_kinds: Sequence[MemoryKind] | None = None,
    integrity: IntegrityGuard | bool | None = None,
    hmac_key: str | bytes | None = None,
    verify_on_retrieve: bool = True,
    drop_unverified: bool = True,
) -> MemoryStore:
    """Factory: JSON-file store under ``path`` (default ``./.mobilegui_ltm``).

    Retrieval modes::

        create_store(path)                           # BM25 (default)
        create_store(path, retriever="hybrid")       # BM25 + hashing vectors
        create_store(path, embedder="hashing")       # same as hybrid
        create_store(path, retriever="vector", embedder=FakeEmbedder())
        create_store(path, embedder="sentence-transformers")  # needs [embed]
        create_store(path, profile="failures-only")  # ablation profile
        create_store(path, kind_weights={"failure_note": 1.3}, expand_hops=1)
        create_store(path, local_rag=[{"app_id": "com.shop", "name": "Shop"}], blend_local_rag=True)
        create_store(path, integrity=True, hmac_key="dev-secret")
    """
    resolved_profile = resolve_profile(profile)
    if resolved_profile is not None:
        enabled = enabled and resolved_profile.enabled
        if write_kinds is None:
            write_kinds = resolved_profile.kinds
        if retrieve_kinds is None:
            retrieve_kinds = resolved_profile.kinds
        if expand_hops is None:
            expand_hops = resolved_profile.expand_hops
    hops = 0 if expand_hops is None else int(expand_hops)
    weights = normalize_kind_weights(kind_weights)
    resolved_embedder = resolve_embedder(embedder)
    resolved_retriever = _resolve_retriever(
        retriever,
        resolved_embedder,
        lexical_weight=lexical_weight,
        vector_weight=vector_weight,
        kind_weights=weights,
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
        write_kinds=write_kinds,
        retrieve_kinds=retrieve_kinds,
        kind_weights=weights,
        expand_hops=hops,
        strict_task=strict_task,
        blocks=blocks,
        local_rag=local_rag,
        blend_local_rag=blend_local_rag,
        local_rag_k=local_rag_k,
        include_candidates=include_candidates,
        include_retired=include_retired,
        prefer_stable=prefer_stable,
        promote_after=promote_after,
        demote_after=demote_after,
        promote_kinds=promote_kinds,
        integrity=integrity,
        hmac_key=hmac_key,
        verify_on_retrieve=verify_on_retrieve,
        drop_unverified=drop_unverified,
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
    kind_weights: dict[str, float] | None = None,
) -> Retriever:
    if retriever is None:
        if embedder is not None:
            return HybridRetriever(
                embedder,
                lexical_weight=lexical_weight,
                vector_weight=vector_weight,
                kind_weights=kind_weights,
            )
        return BM25Retriever(kind_weights=kind_weights)
    if isinstance(retriever, str):
        key = retriever.strip().lower()
        if key in {"bm25", "keyword"}:
            return BM25Retriever(kind_weights=kind_weights)
        if key == "hybrid":
            return HybridRetriever(
                embedder or HashingEmbedder(),
                lexical_weight=lexical_weight,
                vector_weight=vector_weight,
                kind_weights=kind_weights,
            )
        if key in {"vector", "embed", "embedding"}:
            return EmbeddingRetriever(
                embedder or HashingEmbedder(),
                kind_weights=kind_weights,
            )
        raise ValueError(
            f"Unknown retriever {retriever!r}. Use 'bm25', 'hybrid', or 'vector'."
        )
    return retriever


def _collapse_logical_keys(records: Sequence[MemoryRecord]) -> list[MemoryRecord]:
    """Last write wins for the same (kind, logical_key) inside one commit."""
    keep: list[MemoryRecord] = []
    index_for_key: dict[tuple[MemoryKind, str], int] = {}
    for record in records:
        if not record.logical_key:
            keep.append(record)
            continue
        key = (record.kind, record.logical_key)
        if key in index_for_key:
            keep[index_for_key[key]] = record
        else:
            index_for_key[key] = len(keep)
            keep.append(record)
    return keep


def _load_bundle(session: SessionBundle | dict[str, Any] | str | Path) -> SessionBundle:
    if isinstance(session, SessionBundle):
        return session
    if isinstance(session, dict):
        return SessionBundle.model_validate(session)
    path = Path(session)
    return SessionBundle.model_validate_json(path.read_text(encoding="utf-8"))
