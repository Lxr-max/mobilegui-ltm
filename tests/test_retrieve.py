from __future__ import annotations

from mobilegui_ltm.retrieve.embed import EmbeddingRetriever, cosine
from mobilegui_ltm.schema import MemoryKind, MemoryRecord


def test_retrieve_ranks_failure_notes_for_query(store):
    _seed(store)
    hits = store.retrieve(
        "red sneakers ShopX size 9 avoid ShopY",
        task_id="shop-red-sneakers-size9",
        app_ids=["com.example.shopping"],
        k=5,
    )
    assert hits
    blob = " ".join(h.content for h in hits)
    assert "ShopY" in blob
    assert any(h.kind is MemoryKind.FAILURE_NOTE for h in hits)


def test_retrieve_app_filter_excludes_other_apps(store):
    _seed(store)
    hits = store.retrieve("wifi toggle", app_ids=["com.settings"], k=5)
    assert hits
    assert all((not h.app_ids) or ("com.settings" in h.app_ids) for h in hits)


def test_retrieve_disabled_store_is_empty(tmp_path):
    from mobilegui_ltm import create_store

    store = create_store(tmp_path, agent_id="a", enabled=False)
    store.write_attempt("t", 1, traj="x", outcome="fail")
    assert store.retrieve("x") == []


def test_embedding_stub_cosine_and_fallback():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    recs = [
        MemoryRecord(
            kind=MemoryKind.UI_FACT,
            content="alpha",
            task_id="t",
            attempt_k=1,
            agent_id="a",
            embedding=[1.0, 0.0],
        ),
        MemoryRecord(
            kind=MemoryKind.UI_FACT,
            content="beta",
            task_id="t",
            attempt_k=1,
            agent_id="a",
            embedding=[0.0, 1.0],
        ),
    ]
    retriever = EmbeddingRetriever(embed_query=lambda q: [1.0, 0.0])
    ranked = retriever.retrieve(recs, "alpha", k=2)
    assert ranked[0].content == "alpha"

    fallback = EmbeddingRetriever(embed_query=None)
    text_ranked = fallback.retrieve(recs, "beta", k=1)
    assert text_ranked[0].content == "beta"


def _seed(store):
    store.write_attempt(
        "shop-red-sneakers-size9",
        1,
        traj=[
            {
                "app_id": "com.example.shopping",
                "screen": "pdp",
                "action": "tap:ShopY",
                "observation": "size 9 unavailable at ShopY",
            }
        ],
        outcome={
            "status": "failure",
            "reason": (
                "Size 9 unavailable at ShopY. Avoid ShopY; the item is sold by ShopX. "
                "On retry, apply a size 9 filter before opening a product."
            ),
        },
    )
    store.write_attempt(
        "unrelated-settings",
        1,
        traj=[{"app_id": "com.settings", "screen": "wifi", "observation": "wifi toggle"}],
        outcome={"status": "success", "reason": "enabled wifi"},
    )
