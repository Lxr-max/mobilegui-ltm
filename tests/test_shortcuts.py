from __future__ import annotations

from mobilegui_ltm.adapters.dummy import DummyGUIAgent, shopping_task
from mobilegui_ltm.encode import ShortcutEncoder
from mobilegui_ltm.inject import LTM_START
from mobilegui_ltm.schema import AttemptOutcome, MemoryKind, OutcomeStatus, Trajectory


def _success_traj() -> Trajectory:
    return DummyGUIAgent()._success_traj(shopping_task(), attempt_k=2)


def test_shortcut_encoder_from_success_has_actions_and_preconditions():
    traj = _success_traj()
    recs = ShortcutEncoder().encode(
        shopping_task().task_id,
        2,
        traj,
        AttemptOutcome(status=OutcomeStatus.SUCCESS, reason="ok"),
        "agent-a",
    )
    assert recs
    assert all(r.kind is MemoryKind.SHORTCUT for r in recs)
    episode = next(r for r in recs if "episode" in r.tags)
    actions = episode.metadata["actions"]
    assert "apply_filter:size=9" in actions
    assert "tap:add_to_cart" in actions
    assert episode.metadata["preconditions"]
    assert "com.example.shopping" in episode.app_ids
    assert any("skill" in r.tags for r in recs)


def test_shortcuts_not_mined_on_failure():
    traj = DummyGUIAgent()._failure_traj(shopping_task(), attempt_k=1)
    recs = ShortcutEncoder().encode(
        "t",
        1,
        traj,
        AttemptOutcome(status=OutcomeStatus.FAILURE, reason="ShopY"),
        "a",
    )
    assert recs == []


def test_partial_high_progress_emits_shortcut():
    traj = Trajectory(
        steps=_success_traj().steps[:4],
        app_ids=["com.example.shopping"],
        metadata={"subgoals": ["filter then open ShopX"]},
    )
    recs = ShortcutEncoder().encode(
        "t",
        1,
        traj,
        AttemptOutcome(
            status=OutcomeStatus.PARTIAL,
            reason="reached PDP",
            metrics={"progress": 0.85},
        ),
        "a",
    )
    assert recs
    assert any(r.kind is MemoryKind.SHORTCUT for r in recs)


def test_explicit_shortcut_metadata():
    traj = Trajectory(
        steps=_success_traj().steps,
        app_ids=["com.example.shopping"],
        metadata={
            "shortcuts": [
                {
                    "summary": "Apply size 9 then open ShopX",
                    "actions": ["apply_filter:size=9", "tap:ShopX"],
                    "preconditions": ["search results visible"],
                    "subgoal": "open target seller",
                }
            ]
        },
    )
    recs = ShortcutEncoder().encode(
        "t",
        1,
        traj,
        AttemptOutcome(status=OutcomeStatus.SUCCESS),
        "a",
    )
    assert any("Apply size 9 then open ShopX" in r.content for r in recs)
    assert any(r.metadata.get("subgoal") == "open target seller" for r in recs)


def test_encode_store_retrieve_inject_shortcuts(store):
    traj = _success_traj()
    written = store.write_attempt(
        shopping_task().task_id,
        2,
        traj,
        {"status": "success", "reason": "Added ShopX red sneakers size 9 to cart."},
    )
    assert any(r.kind is MemoryKind.SHORTCUT for r in written)
    stored = store.list_memories(kinds=[MemoryKind.SHORTCUT])
    assert stored
    hits = store.retrieve(
        "apply size filter then add ShopX red sneakers to cart",
        task_id=shopping_task().task_id,
        app_ids=["com.example.shopping"],
        k=8,
    )
    assert any(h.kind is MemoryKind.SHORTCUT for h in hits)
    prompt = store.inject("Continue the shopping task.", hits)
    assert LTM_START in prompt
    assert "Shortcuts" in prompt
    blob = prompt.lower()
    assert "apply_filter:size=9" in blob or "add_to_cart" in blob
    assert "preconditions" in blob or "app=" in blob
