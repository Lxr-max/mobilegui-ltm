from __future__ import annotations

import pytest
from pydantic import ValidationError

from mobilegui_ltm.schema import (
    AttemptOutcome,
    AtomicAction,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    RecordStatus,
    ShortcutSpec,
    Trajectory,
    coerce_outcome,
    coerce_trajectory,
    parse_atomic_action,
)


def test_coerce_trajectory_from_list():
    traj = coerce_trajectory(
        [
            {"app": "com.shop", "activity": "search", "action": "type:q", "obs": "results"},
            {"app_id": "com.shop", "screen": "pdp", "action": "tap"},
        ]
    )
    assert isinstance(traj, Trajectory)
    assert traj.app_ids == ["com.shop"]
    assert traj.steps[0].screen == "search"
    assert traj.steps[0].observation == "results"


def test_coerce_trajectory_from_text_and_none():
    assert coerce_trajectory("home screen").steps[0].observation == "home screen"
    assert coerce_trajectory(None).steps == []


def test_coerce_outcome_variants():
    assert coerce_outcome(True).status is OutcomeStatus.SUCCESS
    assert coerce_outcome(False).status is OutcomeStatus.FAILURE
    assert coerce_outcome("partial").status is OutcomeStatus.PARTIAL
    assert coerce_outcome("boom").status is OutcomeStatus.FAILURE
    assert coerce_outcome("boom").reason == "boom"
    assert coerce_outcome({"success": True}).status is OutcomeStatus.SUCCESS
    assert coerce_outcome(AttemptOutcome(status=OutcomeStatus.SUCCESS)).status is OutcomeStatus.SUCCESS


def test_memory_record_searchable_text():
    rec = MemoryRecord(
        kind=MemoryKind.UI_FACT,
        content="ShopX carries size 9",
        task_id="t1",
        attempt_k=1,
        agent_id="a",
        app_ids=["com.shop"],
        tags=["shop"],
    )
    blob = rec.searchable_text()
    assert "ShopX" in blob
    assert "com.shop" in blob
    assert rec.kind is MemoryKind.UI_FACT


def test_invalid_kind_rejected():
    with pytest.raises(ValidationError):
        MemoryRecord(
            kind="not-a-kind",  # type: ignore[arg-type]
            content="x",
            task_id="t",
            attempt_k=1,
            agent_id="a",
        )


def test_parse_atomic_action_and_shortcut_spec():
    action = parse_atomic_action("apply_filter:size=9")
    assert action.type == "apply_filter"
    assert action.args["size"] == "9"
    assert action.as_text() == "apply_filter:size=9"
    spec = ShortcutSpec(
        name="filter_cart",
        description="size then cart",
        preconditions=["app=com.shop"],
        arguments={"size": 9},
        atomic_actions=[action, AtomicAction(type="tap", target="add_to_cart")],
        tags=["skill"],
    )
    rec = MemoryRecord(
        kind=MemoryKind.SHORTCUT,
        content="Shortcut: size then cart",
        task_id="t",
        attempt_k=1,
        agent_id="a",
        status=RecordStatus.ACTIVE,
        metadata={"shortcut": spec.model_dump(mode="json")},
    )
    parsed = ShortcutSpec.from_record(rec)
    assert parsed is not None
    assert parsed.name == "filter_cart"
    assert parsed.action_texts()[0] == "apply_filter:size=9"
