"""BM25 / keyword retriever (MVP default).

Ranks candidate ``MemoryRecord`` objects. Same-task memories receive a score
boost; ``app_ids`` on the query restrict to overlapping (or unscoped) records.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence

from mobilegui_ltm.schema import MemoryRecord
from mobilegui_ltm.retrieve.scoring import (
    kind_weight,
    normalize_kind_weights,
    precondition_bonus,
    stability_bonus,
    tokenize,
)

# Tuned for short memory notes rather than long documents.
_K1 = 1.4
_B = 0.5
_TASK_BOOST = 1.35


class BM25Retriever:
    """In-memory BM25 over already-filtered candidate records."""

    def __init__(
        self,
        *,
        k1: float = _K1,
        b: float = _B,
        task_boost: float = _TASK_BOOST,
        kind_weights: dict | None = None,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.task_boost = task_boost
        self.kind_weights = normalize_kind_weights(kind_weights)

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
        if not candidates:
            return []
        if k <= 0:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            # Recency fallback: newest first, same-task first.
            return sorted(
                candidates,
                key=lambda r: (
                    0 if (task_id and r.task_id == task_id) else 1,
                    -r.created_at.timestamp() if r.created_at else 0,
                ),
            )[:k]

        scores = self._score(
            candidates,
            query_tokens,
            task_id=task_id,
            query=query,
            state=state,
            kind_weights=kind_weights,
            prefer_stable=bool(_kwargs.get("prefer_stable", True)),
        )
        ranked = sorted(
            zip(scores, candidates, strict=True),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [record for score, record in ranked if score > 0][:k] or [
            record for _, record in ranked[:k]
        ]

    def scores(
        self,
        records: Sequence[MemoryRecord],
        query: str,
        *,
        task_id: str | None = None,
        state: object = None,
        kind_weights: dict | None = None,
        apply_bonuses: bool = True,
        prefer_stable: bool = True,
    ) -> list[float]:
        """Raw BM25 scores aligned with ``records`` (no app filter)."""
        if not records:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return [0.0] * len(records)
        return self._score(
            records,
            query_tokens,
            task_id=task_id,
            query=query,
            state=state,
            kind_weights=kind_weights,
            apply_bonuses=apply_bonuses,
            prefer_stable=prefer_stable,
        )

    def _score(
        self,
        records: Sequence[MemoryRecord],
        query_tokens: list[str],
        *,
        task_id: str | None,
        query: str = "",
        state: object = None,
        kind_weights: dict | None = None,
        apply_bonuses: bool = True,
        prefer_stable: bool = True,
    ) -> list[float]:
        docs = [tokenize(r.searchable_text()) for r in records]
        lengths = [max(len(doc), 1) for doc in docs]
        avgdl = sum(lengths) / len(lengths)
        df: Counter[str] = Counter()
        for doc in docs:
            df.update(set(doc))
        n = len(docs)
        idf = {
            term: math.log(1.0 + (n - df[term] + 0.5) / (df[term] + 0.5))
            for term in set(query_tokens)
        }

        scores: list[float] = []
        q_counts = Counter(query_tokens)
        for record, doc, length in zip(records, docs, lengths, strict=True):
            tf = Counter(doc)
            score = 0.0
            for term, qtf in q_counts.items():
                freq = tf.get(term, 0)
                if freq == 0:
                    continue
                denom = freq + self.k1 * (1.0 - self.b + self.b * length / avgdl)
                score += idf.get(term, 0.0) * (freq * (self.k1 + 1.0) / denom) * qtf
            if task_id and record.task_id == task_id:
                score *= self.task_boost
            if apply_bonuses:
                weights = normalize_kind_weights(kind_weights or self.kind_weights)
                score *= kind_weight(record, weights)
                score *= precondition_bonus(record, query, state)
                score *= stability_bonus(record, prefer_stable=prefer_stable)
            scores.append(score)
        return scores


class KeywordRetriever(BM25Retriever):
    """Alias kept for callers who prefer the 'keyword' name from the brief."""


def _filter_apps(
    records: list[MemoryRecord],
    app_ids: Sequence[str] | None,
) -> list[MemoryRecord]:
    if not app_ids:
        return records
    wanted = set(app_ids)
    kept: list[MemoryRecord] = []
    for record in records:
        if not record.app_ids or not wanted.isdisjoint(record.app_ids):
            kept.append(record)
    return kept
