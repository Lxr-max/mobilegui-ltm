from __future__ import annotations

import pytest
from pydantic import ValidationError

from mobilegui_ltm.schema import (
    AttemptOutcome,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    Trajectory,
    coerce_outcome,
    coerce_trajectory,
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
