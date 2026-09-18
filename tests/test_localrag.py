from __future__ import annotations

from mobilegui_ltm import create_store
from mobilegui_ltm.adapters.android_world import AndroidWorldAdapter
from mobilegui_ltm.localrag import CatalogLocalRAG, NullLocalRAG
from mobilegui_ltm.schema import MemoryKind


def test_local_rag_default_off_does_not_blend(store):
    assert isinstance(store.local_rag, NullLocalRAG)
    store.remember("Avoid ShopY", kind=MemoryKind.FAILURE_NOTE, task_id="t")
    hits = store.retrieve("shopping cart ShopX", k=8)
    assert all(h.kind is not MemoryKind.APP_PRIOR for h in hits)


def test_fake_catalog_blends_with_ltm(tmp_path):
    catalog = [
        {
            "app_id": "com.example.shopping",
            "name": "Shopping",
            "summary": "Search products and add to cart",
            "capabilities": ["search", "cart", "filter"],
        },
        {
            "app_id": "com.settings",
            "name": "Settings",
            "summary": "Wifi and airplane mode",
            "capabilities": ["wifi"],
        },
    ]
    store = create_store(
        tmp_path,
        agent_id="a",
        local_rag=catalog,
        blend_local_rag=True,
        local_rag_k=3,
    )
    store.remember(
        "Avoid ShopY; prefer ShopX size 9 filter",
        kind=MemoryKind.FAILURE_NOTE,
        task_id="shop",
        app_ids=["com.example.shopping"],
    )
    hits = store.retrieve(
        "shopping cart ShopX",
        task_id="shop",
        app_ids=["com.example.shopping"],
        k=5,
    )
    kinds = {h.kind for h in hits}
    assert MemoryKind.FAILURE_NOTE in kinds
    assert MemoryKind.APP_PRIOR in kinds
    assert any("com.example.shopping" in h.app_ids for h in hits if h.kind is MemoryKind.APP_PRIOR)
    assert all(
        "com.settings" not in h.app_ids
        for h in hits
        if h.kind is MemoryKind.APP_PRIOR
    )


def test_register_app_prior_without_adb(tmp_path):
    store = create_store(tmp_path, agent_id="aw", blend_local_rag=True)
    adapter = AndroidWorldAdapter(store)
    fact = adapter.register_app_prior(
        "com.example.shopping",
        name="Shopping",
        summary="Buy sneakers",
        capabilities=["search", "cart"],
    )
    assert fact.app_id == "com.example.shopping"
    assert isinstance(store.local_rag, CatalogLocalRAG)
    hits = store.retrieve("sneakers cart Shopping", k=4, blend_local_rag=True)
    assert any(h.kind is MemoryKind.APP_PRIOR for h in hits)


def test_catalog_lookup_is_offline():
    rag = CatalogLocalRAG(
        [{"app_id": "com.shop", "name": "Shop", "capabilities": ["checkout"]}]
    )
    assert rag.lookup("checkout shop", k=1)[0].app_id == "com.shop"
    assert rag.lookup("wifi airplane", k=3) == []
