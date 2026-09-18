"""Optional embedding retriever (phase-2 stub).

No model is bundled. If the caller supplies ``embed_query`` and records already
carry ``MemoryRecord.embedding``, cosine similarity is used. Otherwise the
optional ``fallback`` retriever (BM25 by default) handles ranking.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from mobilegui_ltm.schema import MemoryRecord
from mobilegui_ltm.retrieve.keyword import BM25Retriever, _filter_apps


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class EmbeddingRetriever:
    """Vector ranker with BM25 fallback when embeddings are absent."""

    def __init__(
        self,
        embed_query: Callable[[str], list[float]] | None = None,
        *,
        fallback: BM25Retriever | None = None,
    ) -> None:
        self.embed_query = embed_query
        self.fallback = fallback if fallback is not None else BM25Retriever()

    def retrieve(
        self,
        records: Sequence[MemoryRecord],
        query: str,
        *,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
        k: int = 5,
    ) -> list[MemoryRecord]:
        candidates = _filter_apps(list(records), app_ids)
        if not candidates or k <= 0:
            return []
        if self.embed_query is None:
            return self.fallback.retrieve(
                candidates, query, task_id=task_id, app_ids=None, k=k
            )
        query_vec = self.embed_query(query)
        scored: list[tuple[float, MemoryRecord]] = []
        missing: list[MemoryRecord] = []
        for record in candidates:
            if record.embedding:
                score = cosine(query_vec, record.embedding)
                if task_id and record.task_id == task_id:
                    score += 0.05
                scored.append((score, record))
            else:
                missing.append(record)
        scored.sort(key=lambda pair: pair[0], reverse=True)
        ranked = [record for _, record in scored]
        if len(ranked) < k and missing:
            extra = self.fallback.retrieve(
                missing, query, task_id=task_id, app_ids=None, k=k - len(ranked)
            )
            ranked.extend(extra)
        return ranked[:k]
