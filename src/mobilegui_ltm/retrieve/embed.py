"""Vector and hybrid retrievers (BM25 + embeddings).

``HybridRetriever`` is the phase-2 default when an embedder is configured.
``EmbeddingRetriever`` is vector-only (lexical weight 0) with BM25 fallback
for records that still lack vectors.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from mobilegui_ltm.retrieve.embedder import (
    CallableEmbedder,
    Embedder,
    HashingEmbedder,
    cosine,
    resolve_embedder,
)
from mobilegui_ltm.retrieve.keyword import BM25Retriever, _filter_apps
from mobilegui_ltm.retrieve.scoring import kind_weight, normalize_kind_weights, precondition_bonus
from mobilegui_ltm.schema import MemoryRecord

# Re-export cosine for existing tests.
__all__ = ["EmbeddingRetriever", "HybridRetriever", "cosine"]


def _minmax(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi - lo < 1e-12:
        return [0.0 if v == 0.0 else 1.0 for v in values]
    return [(v - lo) / (hi - lo) for v in values]


class HybridRetriever:
    """Combine BM25 and cosine scores with configurable weights."""

    def __init__(
        self,
        embedder: Embedder | str | Callable[[str], list[float]] | None = None,
        *,
        lexical_weight: float = 0.5,
        vector_weight: float = 0.5,
        task_boost: float = 1.25,
        fallback: BM25Retriever | None = None,
        kind_weights: dict | None = None,
    ) -> None:
        resolved = resolve_embedder(embedder) if embedder is not None else HashingEmbedder()
        self.embedder: Embedder = resolved or HashingEmbedder()
        self.lexical_weight = float(lexical_weight)
        self.vector_weight = float(vector_weight)
        self.task_boost = task_boost
        self.kind_weights = kind_weights
        self.fallback = fallback if fallback is not None else BM25Retriever(kind_weights=kind_weights)

    def retrieve(
        self,
        records: Sequence[MemoryRecord],
        query: str,
        *,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
        k: int = 5,
        kind_weights: dict | None = None,
        state: object = None,
        **_kwargs: object,
    ) -> list[MemoryRecord]:
        candidates = _filter_apps(list(records), app_ids)
        if not candidates or k <= 0:
            return []
        weights = normalize_kind_weights(kind_weights or self.kind_weights)
        if self.vector_weight <= 0 and self.lexical_weight > 0:
            return self.fallback.retrieve(
                candidates,
                query,
                task_id=task_id,
                app_ids=None,
                k=k,
                kind_weights=weights,
                state=state,
            )

        query_vec = self.embedder.embed_query(query)
        lexical = self.fallback.scores(
            candidates,
            query,
            task_id=None,
            state=state,
            kind_weights=weights,
            apply_bonuses=False,
        )
        vector = [self._vector_score(record, query_vec) for record in candidates]
        lex_n = _minmax(lexical)
        vec_n = _minmax(vector)
        fused: list[tuple[float, MemoryRecord]] = []
        for record, l_score, v_score in zip(candidates, lex_n, vec_n, strict=True):
            score = self.lexical_weight * l_score + self.vector_weight * v_score
            if task_id and record.task_id == task_id:
                score *= self.task_boost
            score *= kind_weight(record, weights)
            score *= precondition_bonus(record, query, state)
            fused.append((score, record))
        fused.sort(key=lambda pair: pair[0], reverse=True)
        positive = [record for score, record in fused if score > 0]
        if positive:
            return positive[:k]
        return [record for _, record in fused[:k]]

    def _vector_score(self, record: MemoryRecord, query_vec: Sequence[float]) -> float:
        vec = record.embedding
        if not vec:
            vec = self.embedder.embed_query(record.searchable_text())
            record.embedding = vec
        return max(0.0, cosine(query_vec, vec))


class EmbeddingRetriever(HybridRetriever):
    """Vector-first ranker. BM25 fills in only when no usable vectors exist."""

    def __init__(
        self,
        embedder: Embedder | str | Callable[[str], list[float]] | None = None,
        embed_query: Callable[[str], list[float]] | None = None,
        *,
        fallback: BM25Retriever | None = None,
        lexical_weight: float = 0.0,
        vector_weight: float = 1.0,
        kind_weights: dict | None = None,
    ) -> None:
        if embedder is None and embed_query is not None:
            embedder = CallableEmbedder(embed_query)
        super().__init__(
            embedder,
            lexical_weight=lexical_weight,
            vector_weight=vector_weight,
            fallback=fallback,
            kind_weights=kind_weights,
        )
