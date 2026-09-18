from __future__ import annotations

from mobilegui_ltm import create_store
from mobilegui_ltm.encode import ShortcutEncoder, TrajectorySummarizer
from mobilegui_ltm.schema import (
    AttemptOutcome,
    MemoryKind,
    OutcomeStatus,
    Trajectory,
    TrajectoryStep,
    coerce_outcome,
    coerce_trajectory,
)


def _sample_fail():
    traj = coerce_trajectory(
        [
            {
                "app_id": "com.example.shopping",
                "screen": "pdp",
                "action": "tap:ShopY",
                "observation": "size 9 unavailable at ShopY; ShopX listed in search",
            }
        ]
    )
    traj.metadata["ui_facts"] = ["Search ranked ShopY above ShopX"]
    outcome = coerce_outcome(
        {
            "status": "failure",
            "reason": "Size 9 unavailable at ShopY. Avoid ShopY; sold by ShopX.",
        }
    )
    return traj, outcome


def test_encoder_writes_ui_facts_subgoal_and_failure(store):
    traj, outcome = _sample_fail()
    recs = store.write_attempt("task-1", 1, traj, outcome)
    kinds = {r.kind for r in recs}
    assert MemoryKind.UI_FACT in kinds
    assert MemoryKind.SUBGOAL_TRACE in kinds
    assert MemoryKind.FAILURE_NOTE in kinds
    assert MemoryKind.SHORTCUT not in kinds
    contents = " ".join(r.content for r in recs)
    assert "ShopY" in contents
    assert "com.example.shopping" in contents


def test_success_does_not_require_failure_notes():
    encoder = TrajectorySummarizer()
    traj = Trajectory(
        steps=[TrajectoryStep(screen="cart", action="done", observation="ok")],
        app_ids=["com.shop"],
    )
    recs = encoder.encode(
        "t", 2, traj, AttemptOutcome(status=OutcomeStatus.SUCCESS, reason="ok"), "a"
    )
    assert any(r.kind is MemoryKind.SUBGOAL_TRACE for r in recs)
    assert all(r.kind is not MemoryKind.FAILURE_NOTE for r in recs)


def test_shortcuts_phase2_stub_empty():
    traj, outcome = _sample_fail()
    assert ShortcutEncoder().encode("t", 1, traj, outcome, "a") == []
    recs = TrajectorySummarizer(emit_shortcuts=True).encode("t", 1, traj, outcome, "a")
    assert all(r.kind is not MemoryKind.SHORTCUT for r in recs)
