"""Embedder protocol and implementations.

Default local backend is a hashing-trick encoder (no downloads). Tests use
``FakeEmbedder``. Neural models are optional via ``pip install mobilegui-ltm[embed]``.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

from mobilegui_ltm.retrieve.keyword import tokenize


def l2_normalize(vector: Sequence[float]) -> list[float]:
    values = [float(x) for x in vector]
    norm = math.sqrt(sum(x * x for x in values))
    if norm == 0.0:
        return values
    return [x / norm for x in values]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@runtime_checkable
class Embedder(Protocol):
    """Maps text → dense vector. Swap hashing / fake / sentence-transformers."""

    dim: int
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Signed hashing-trick encoder. Deterministic, local, no model files."""

    def __init__(self, dim: int = 128, *, name: str = "hashing") -> None:
        if dim < 4:
            raise ValueError("dim must be >= 4")
        self.dim = dim
        self.name = f"{name}:{dim}"

    def embed_query(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        features = list(tokens)
        for left, right in zip(tokens, tokens[1:]):
            features.append(f"{left}_{right}")
        if not features:
            return vec
        for feat in features:
            digest = hashlib.md5(feat.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return l2_normalize(vec)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


class FakeEmbedder:
    """Offline test embedder: optional substring overrides + hashing fallback.

    CI must never download weights. Pass ``overrides`` to pin known phrases to
    near-orthogonal fixture vectors.
    """

    def __init__(
        self,
        dim: int = 8,
        overrides: dict[str, Sequence[float]] | None = None,
        *,
        name: str = "fake",
    ) -> None:
        self.dim = dim
        self.name = f"{name}:{dim}"
        self._hash = HashingEmbedder(dim=dim, name=name)
        self.overrides: dict[str, list[float]] = {}
        for needle, vector in (overrides or {}).items():
            padded = list(vector) + [0.0] * max(0, dim - len(vector))
            self.overrides[needle.lower()] = l2_normalize(padded[:dim])

    def embed_query(self, text: str) -> list[float]:
        key = (text or "").lower()
        for needle, vector in sorted(self.overrides.items(), key=lambda kv: -len(kv[0])):
            if needle and needle in key:
                return list(vector)
        return self._hash.embed_query(text)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


class CallableEmbedder:
    """Wrap a ``text -> vector`` function as an :class:`Embedder`."""

    def __init__(
        self,
        fn: Callable[[str], list[float]],
        *,
        dim: int | None = None,
        name: str = "callable",
    ) -> None:
        self._fn = fn
        probe = fn("")
        self.dim = dim or (len(probe) if probe else 8)
        self.name = name

    def embed_query(self, text: str) -> list[float]:
        vec = list(self._fn(text))
        if len(vec) < self.dim:
            vec = vec + [0.0] * (self.dim - len(vec))
        return l2_normalize(vec[: self.dim])

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


class SentenceTransformerEmbedder:
    """Optional neural embedder. Requires ``pip install mobilegui-ltm[embed]``.

    Not used in default tests or CI (downloads weights).
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - exercised when extra missing
            raise ImportError(
                "SentenceTransformerEmbedder requires the optional extra: "
                "pip install 'mobilegui-ltm[embed]'"
            ) from exc
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.name = f"sbert:{model_name}"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(list(texts), convert_to_numpy=True)
        return [l2_normalize(row.tolist()) for row in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


def resolve_embedder(embedder: Embedder | str | Callable[[str], list[float]] | None) -> Embedder | None:
    """Accept an Embedder, alias string, or raw callable."""
    if embedder is None:
        return None
    if isinstance(embedder, str):
        key = embedder.strip().lower()
        if key in {"hashing", "hash", "local"}:
            return HashingEmbedder()
        if key in {"fake", "test"}:
            return FakeEmbedder()
        if key in {"sentence-transformers", "sbert", "st", "minilm"}:
            return SentenceTransformerEmbedder()
        raise ValueError(
            f"Unknown embedder {embedder!r}. Use 'hashing', 'fake', or "
            f"'sentence-transformers', or pass an Embedder instance."
        )
    if isinstance(embedder, type):
        embedder = embedder()
    if hasattr(embedder, "embed_query") and hasattr(embedder, "embed"):
        return embedder  # type: ignore[return-value]
    if callable(embedder):
        return CallableEmbedder(embedder)  # type: ignore[arg-type]
    raise TypeError(f"Unsupported embedder type: {type(embedder)!r}")
