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


def test_embedding_retriever_callable_and_cosine():
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

    fallback = EmbeddingRetriever(embed_query=lambda q: [0.0, 1.0], lexical_weight=1.0, vector_weight=0.0)
    text_ranked = fallback.retrieve(recs, "beta", k=1)
    assert text_ranked[0].content == "beta"


def test_retrieve_kind_weights_can_demote_failures(store):
    _seed(store)
    store.remember(
        "neutral ui fact about sneakers cart",
        kind=MemoryKind.UI_FACT,
        key="ui:neutral",
        task_id="shop-red-sneakers-size9",
        app_ids=["com.example.shopping"],
    )
    boosted = store.retrieve(
        "ShopY size",
        task_id="shop-red-sneakers-size9",
        k=8,
        kind_weights={
            "failure_note": 4.0,
            "ui_fact": 0.05,
            "subgoal_trace": 0.05,
            "causal_anchor": 0.05,
            "shortcut": 0.05,
        },
    )
    assert boosted[0].kind is MemoryKind.FAILURE_NOTE
    demoted = store.retrieve(
        "ShopY size",
        task_id="shop-red-sneakers-size9",
        k=8,
        kind_weights={
            "failure_note": 0.01,
            "ui_fact": 4.0,
            "subgoal_trace": 0.05,
            "causal_anchor": 0.05,
            "shortcut": 0.05,
        },
    )
    assert demoted[0].kind is MemoryKind.UI_FACT


def test_retrieve_screen_filter_keeps_unscoped(store):
    store.remember(
        "chip on search",
        key="ui:search-chip",
        screen="search",
        app_ids=["com.example.shopping"],
        task_id="t",
    )
    store.remember(
        "chip on pdp",
        key="ui:pdp-chip",
        screen="pdp",
        app_ids=["com.example.shopping"],
        task_id="t",
    )
    store.remember(
        "global account state",
        key="ui:account",
        app_ids=["com.example.shopping"],
        task_id="t",
    )
    hits = store.retrieve("chip account", screen="search", k=8)
    screens = {h.screen for h in hits}
    assert "pdp" not in screens
    assert any(h.logical_key == "ui:account" for h in hits)
    assert any(h.logical_key == "ui:search-chip" for h in hits)


def test_retrieve_strict_task_filter(store):
    store.remember("alpha task note", key="ui:a", task_id="alpha")
    store.remember("beta task note", key="ui:b", task_id="beta")
    loose = store.retrieve("task note", task_id="alpha", k=8, strict_task=False)
    assert {h.task_id for h in loose} >= {"alpha", "beta"}
    strict = store.retrieve("task note", task_id="alpha", k=8, strict_task=True)
    assert all(h.task_id == "alpha" for h in strict)


def test_shortcut_precondition_soft_match(store):
    matching = MemoryRecord(
        kind=MemoryKind.SHORTCUT,
        content="Shortcut: filter then cart",
        task_id="t",
        attempt_k=1,
        agent_id=store.agent_id,
        metadata={
            "shortcut": {
                "name": "filter_cart",
                "description": "apply size then cart",
                "preconditions": ["app=com.shop", "start_screen=search"],
                "arguments": {"size": 9},
                "atomic_actions": [{"type": "apply_filter", "args": {"size": 9}}],
                "tags": ["skill"],
            }
        },
    )
    other = MemoryRecord(
        kind=MemoryKind.SHORTCUT,
        content="Shortcut: wifi toggle",
        task_id="t",
        attempt_k=1,
        agent_id=store.agent_id,
        metadata={
            "shortcut": {
                "name": "wifi_on",
                "description": "toggle wifi",
                "preconditions": ["app=com.settings", "start_screen=wifi"],
                "arguments": {},
                "atomic_actions": [{"type": "tap", "target": "wifi"}],
                "tags": ["skill"],
            }
        },
    )
    store.backend.upsert([matching, other])
    hits = store.retrieve(
        "apply size filter",
        task_id="t",
        k=2,
        state={"app": "com.shop", "screen": "search"},
    )
    assert hits
    spec_name = hits[0].metadata.get("shortcut", {}).get("name") or hits[0].metadata.get("name")
    assert spec_name == "filter_cart"
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
