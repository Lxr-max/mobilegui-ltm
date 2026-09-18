from __future__ import annotations

from mobilegui_ltm.adapters.dummy import DummyGUIAgent, shopping_task
from mobilegui_ltm.encode import ShortcutEncoder
from mobilegui_ltm.inject import LTM_START
from mobilegui_ltm.schema import (
    AttemptOutcome,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    ShortcutSpec,
    Trajectory,
)


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
    spec = ShortcutSpec.from_record(episode)
    assert spec is not None
    assert spec.atomic_actions
    assert spec.arguments
    assert episode.metadata.get("shortcut")


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
    assert "call " in blob
    spec = next(r for r in stored if "episode" in r.tags)
    parsed = ShortcutSpec.from_record(spec)
    assert parsed is not None
    assert parsed.name
    assert parsed.atomic_actions
    assert parsed.preconditions
    worker = store.format_worker_shortcuts([spec])
    assert worker.startswith("- [shortcut")
    assert "call " in worker


def test_shortcut_spec_dual_read_legacy_text():
    rec = MemoryRecord(
        kind=MemoryKind.SHORTCUT,
        content="Shortcut: apply size then cart",
        task_id="t",
        attempt_k=1,
        agent_id="a",
        metadata={
            "name": "apply_size_cart",
            "actions": ["apply_filter:size=9", "tap:add_to_cart"],
            "preconditions": ["app=com.shop", "start_screen=search"],
        },
    )
    spec = ShortcutSpec.from_record(rec)
    assert spec is not None
    assert spec.name == "apply_size_cart"
    assert [a.as_text() for a in spec.atomic_actions] == [
        "apply_filter:size=9",
        "tap:add_to_cart",
    ]
    assert "app=com.shop" in spec.preconditions


def test_explicit_executable_shortcut_dict():
    traj = Trajectory(
        steps=_success_traj().steps,
        app_ids=["com.example.shopping"],
        metadata={
            "shortcuts": [
                {
                    "name": "open_shopx_pdp",
                    "description": "Filter size 9 then open ShopX",
                    "preconditions": ["search results visible"],
                    "arguments": {"size": 9, "seller": "ShopX"},
                    "atomic_actions": [
                        {"type": "apply_filter", "args": {"size": 9}},
                        {"type": "tap", "target": "ShopX"},
                    ],
                    "tags": ["task", "shopping"],
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
    named = next(r for r in recs if r.logical_key == "shortcut:open_shopx_pdp")
    spec = named.metadata["shortcut"]
    assert spec["name"] == "open_shopx_pdp"
    assert spec["atomic_actions"]
    assert spec["arguments"]["seller"] == "ShopX"
