from __future__ import annotations

from mobilegui_ltm import create_store
from mobilegui_ltm.schema import MemoryKind, Stability


def test_new_shortcut_starts_candidate_and_promote_demote(store):
    rec = store.remember(
        "Shortcut: apply size then cart",
        kind=MemoryKind.SHORTCUT,
        key="shortcut:filter_cart",
        task_id="shop",
        metadata={"actions": ["apply_filter:size=9", "tap:add_to_cart"]},
    )
    assert rec is not None
    assert rec.stability is Stability.CANDIDATE
    promoted = store.promote("shortcut:filter_cart")
    assert promoted is not None
    assert promoted.stability is Stability.STABLE
    demoted = store.demote("shortcut:filter_cart")
    assert demoted is not None
    assert demoted.stability is Stability.CANDIDATE
    retired = store.demote("shortcut:filter_cart")
    assert retired is not None
    assert retired.stability is Stability.RETIRED
    assert store.retrieve("apply size cart", task_id="shop", k=5) == []
    found = store.retrieve(
        "apply size cart", task_id="shop", k=5, include_candidates=True
    )
    # retired still excluded unless include_retired
    assert found == []
    store.include_retired = True
    revived = store.retrieve("apply size cart", task_id="shop", k=5)
    assert revived and revived[0].stability is Stability.RETIRED


def test_retrieve_can_hide_candidates(tmp_path):
    store = create_store(tmp_path, agent_id="a", include_candidates=False)
    store.remember(
        "Shortcut: candidate only",
        kind=MemoryKind.SHORTCUT,
        key="shortcut:cand",
        task_id="t",
        metadata={"actions": ["tap:x"]},
    )
    store.remember("stable ui fact ShopX", key="ui:seller", task_id="t")
    hits = store.retrieve("ShopX candidate", task_id="t", k=8)
    assert all(h.kind is not MemoryKind.SHORTCUT for h in hits)
    with_cands = store.retrieve(
        "ShopX candidate", task_id="t", k=8, include_candidates=True
    )
    assert any(h.kind is MemoryKind.SHORTCUT for h in with_cands)


def test_auto_promote_after_n_successes(tmp_path):
    store = create_store(tmp_path, agent_id="a", promote_after=2, demote_after=2)
    traj_ok = [
        {
            "app_id": "com.shop",
            "screen": "cart",
            "action": "tap:add_to_cart",
            "observation": "ok",
        }
    ]
    store.write_attempt("t", 1, traj_ok, True)
    shortcut = store.get("shortcut:cart") or next(
        r for r in store.list_memories(kinds=[MemoryKind.SHORTCUT]) if r.is_active()
    )
    assert shortcut.stability is Stability.CANDIDATE
    assert shortcut.success_count >= 1
    store.write_attempt("t", 2, traj_ok, True)
    again = store.get(shortcut.logical_key or shortcut.id)
    assert again is not None
    assert again.success_count >= 2
    assert again.stability is Stability.STABLE


def test_auto_demote_on_repeated_failure(store):
    rec = store.remember(
        "Shortcut: risky tap",
        kind=MemoryKind.SHORTCUT,
        key="shortcut:risky",
        task_id="t",
        stability=Stability.STABLE,
        metadata={"actions": ["tap:bad"]},
    )
    assert rec is not None
    store.write_attempt(
        "t",
        1,
        traj=[{"app_id": "com.shop", "action": "tap:bad", "observation": "fail"}],
        outcome=False,
    )
    store.write_attempt(
        "t",
        2,
        traj=[{"app_id": "com.shop", "action": "tap:bad", "observation": "fail"}],
        outcome=False,
    )
    after = store.get("shortcut:risky")
    assert after is not None
    assert after.fail_count >= 2
    assert after.stability is Stability.CANDIDATE
