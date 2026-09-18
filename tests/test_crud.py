from __future__ import annotations

from mobilegui_ltm.schema import MemoryKind, RecordStatus


def test_remember_same_key_supersedes_contradictory_fact(store):
    first = store.remember(
        "Seller is ShopY",
        kind=MemoryKind.UI_FACT,
        key="ui:seller",
        task_id="shop",
        app_ids=["com.shop"],
        screen="search",
    )
    assert first is not None
    second = store.remember(
        "Seller is ShopX",
        kind=MemoryKind.UI_FACT,
        key="ui:seller",
        task_id="shop",
        app_ids=["com.shop"],
        screen="search",
    )
    assert second is not None
    assert second.id != first.id

    active = store.get("ui:seller")
    assert active is not None
    assert active.content == "Seller is ShopX"
    assert active.status is RecordStatus.ACTIVE

    stale = store.get(first.id, include_inactive=True)
    assert stale is not None
    assert stale.status is RecordStatus.SUPERSEDED
    assert stale.superseded_by == second.id

    hits = store.retrieve("seller ShopY ShopX", task_id="shop", k=8)
    blob = " ".join(h.content for h in hits)
    assert "ShopX" in blob
    assert "ShopY" not in blob


def test_update_by_logical_key(store):
    store.remember("filter=size:7", key="ui:filters", task_id="shop", screen="search")
    updated = store.update("ui:filters", content="filter=size:9", screen="results")
    assert updated is not None
    assert updated.content == "filter=size:9"
    assert updated.screen == "results"
    assert store.get("ui:filters").content == "filter=size:9"


def test_soft_delete_hides_from_retrieve(store):
    rec = store.remember("account logged out", key="ui:account", task_id="shop")
    assert rec is not None
    assert store.delete("ui:account") == 1
    assert store.get("ui:account") is None
    hidden = store.get(rec.id, include_inactive=True)
    assert hidden is not None
    assert hidden.status is RecordStatus.DELETED
    assert store.retrieve("account logged out", k=5) == []
    listed = store.list_memories(include_inactive=True)
    assert any(r.status is RecordStatus.DELETED for r in listed)


def test_hard_delete_removes_record(store):
    rec = store.remember("temp note", key="ui:temp")
    assert rec is not None
    assert store.delete(rec.id, hard=True) == 1
    assert store.get(rec.id, include_inactive=True) is None
    assert store.list_memories(include_inactive=True) == []


def test_write_attempt_supersedes_ui_logical_keys(store):
    store.write_attempt(
        "shop",
        1,
        traj=[{"app_id": "com.shop", "screen": "pdp", "observation": "ShopY listed first"}],
        outcome={"status": "failure", "reason": "ShopY missing size 9"},
    )
    store.write_attempt(
        "shop",
        2,
        traj=[{"app_id": "com.shop", "screen": "pdp", "observation": "ShopX listed first"}],
        outcome={"status": "failure", "reason": "still browsing"},
    )
    active_sellers = [
        r
        for r in store.list_memories(kinds=[MemoryKind.UI_FACT])
        if r.logical_key == "ui:sellers"
    ]
    assert len(active_sellers) == 1
    assert "ShopX" in active_sellers[0].content
    history = [
        r
        for r in store.list_memories(kinds=[MemoryKind.UI_FACT], include_inactive=True)
        if r.logical_key == "ui:sellers"
    ]
    assert len(history) == 2
    # Failure notes accumulate (no logical key) so attempt 1 is still listed.
    notes = store.list_memories(kinds=[MemoryKind.FAILURE_NOTE])
    assert any(r.attempt_k == 1 for r in notes)
    assert any(r.attempt_k == 2 for r in notes)
