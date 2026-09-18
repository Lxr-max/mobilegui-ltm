from __future__ import annotations

import json
from pathlib import Path

import pytest

from mobilegui_ltm import create_store
from mobilegui_ltm.adapters import DummyGUIAgent, PassAtKRunner, shopping_task
from mobilegui_ltm.retrieve.embed import EmbeddingRetriever, HybridRetriever, cosine
from mobilegui_ltm.retrieve.embedder import (
    FakeEmbedder,
    HashingEmbedder,
    SentenceTransformerEmbedder,
)
from mobilegui_ltm.schema import MemoryKind, MemoryRecord


def _rec(content: str, embedding: list[float] | None = None) -> MemoryRecord:
    return MemoryRecord(
        kind=MemoryKind.UI_FACT,
        content=content,
        task_id="t",
        attempt_k=1,
        agent_id="a",
        embedding=embedding,
    )


def test_hashing_embedder_is_deterministic_and_offline():
    enc = HashingEmbedder(dim=64)
    a = enc.embed_query("red sneakers ShopX size 9")
    b = enc.embed_query("red sneakers ShopX size 9")
    c = enc.embed_query("wifi settings toggle airplane mode")
    assert a == b
    assert len(a) == 64
    assert cosine(a, a) == pytest.approx(1.0)
    assert cosine(a, c) < cosine(a, enc.embed_query("ShopX sneakers size 9 cart"))


def test_fake_embedder_overrides_rank_vector_only():
    fake = FakeEmbedder(
        dim=4,
        overrides={
            "alpha topic": [1.0, 0.0, 0.0, 0.0],
            "beta topic": [0.0, 1.0, 0.0, 0.0],
            "alpha query": [1.0, 0.0, 0.0, 0.0],
        },
    )
    recs = [
        _rec("alpha topic note about widgets"),
        _rec("beta topic note about widgets"),
    ]
    recs = [
        r.model_copy(update={"embedding": fake.embed_query(r.content)}) for r in recs
    ]
    ranked = EmbeddingRetriever(fake).retrieve(recs, "alpha query", k=2)
    assert ranked[0].content.startswith("alpha")


def test_hybrid_combines_bm25_and_vectors():
    fake = FakeEmbedder(
        dim=4,
        overrides={
            "shared token": [0.0, 0.0, 1.0, 0.0],
            "vector-only match": [1.0, 0.0, 0.0, 0.0],
            "probe": [1.0, 0.0, 0.0, 0.0],
        },
    )
    recs = [
        _rec("shared token lexical document"),
        _rec("vector-only match shared token"),
    ]
    recs = [
        r.model_copy(update={"embedding": fake.embed_query(r.content)}) for r in recs
    ]
    hybrid = HybridRetriever(fake, lexical_weight=0.15, vector_weight=0.85)
    ranked = hybrid.retrieve(recs, "probe vector-only match", k=2)
    assert "vector-only" in ranked[0].content


def test_create_store_hybrid_persists_embeddings_and_sidecar(tmp_path):
    fake = FakeEmbedder(dim=8)
    store = create_store(tmp_path, agent_id="agent-a", embedder=fake)
    written = store.write_attempt(
        "shop",
        1,
        traj=[{"app_id": "com.shop", "action": "tap:ShopX", "observation": "ok"}],
        outcome=True,
    )
    assert written
    assert all(r.embedding for r in written)
    payload = json.loads(Path(store.backend.path_for("agent-a")).read_text(encoding="utf-8"))
    assert payload["memories"][0]["embedding"]
    sidecar = tmp_path / "agent-a.vectors.json"
    assert sidecar.exists()
    side = json.loads(sidecar.read_text(encoding="utf-8"))
    assert side["embedder"].startswith("fake")
    assert side["vectors"]

    # Sidecar must not be ingested as memories.
    listed = store.list_memories()
    assert listed
    assert all(r.kind in MemoryKind for r in listed)

    n = store.rebuild_index()
    assert n == len(listed)


def test_rebuild_index_requires_embedder(tmp_path):
    store = create_store(tmp_path)
    store.write_attempt("t", 1, traj="x", outcome=False)
    with pytest.raises(RuntimeError, match="embedder"):
        store.rebuild_index()


def test_import_stamps_embeddings_with_store_embedder(tmp_path):
    src = create_store(tmp_path / "src", agent_id="a")
    src.write_attempt("t", 1, traj="hello wifi", outcome=False)
    dest = create_store(tmp_path / "dest", agent_id="a", retriever="hybrid", embedder="fake")
    dest.import_session(src.export_session())
    recs = dest.list_memories()
    assert recs
    assert all(r.embedding for r in recs)


def test_hybrid_pass_at_k_still_recovers(tmp_path):
    store = create_store(tmp_path, agent_id="dummy", retriever="hybrid", embedder="fake")
    report = PassAtKRunner(store, DummyGUIAgent()).run(shopping_task(), k=2)
    assert report.attempts[0].success is False
    assert report.attempts[1].success is True
    assert any(r.kind is MemoryKind.SHORTCUT for r in store.list_memories())


def test_sentence_transformer_optional_extra():
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError, match="mobilegui-ltm\\[embed\\]"):
            SentenceTransformerEmbedder()
    else:
        pytest.skip("sentence-transformers installed; extra is available")


def test_create_store_unknown_retriever(tmp_path):
    with pytest.raises(ValueError, match="retriever"):
        create_store(tmp_path, retriever="graph")
