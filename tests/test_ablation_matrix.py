from __future__ import annotations

from mobilegui_ltm import create_store
from mobilegui_ltm.adapters import (
    DummyGUIAgent,
    PassAtKRunner,
    run_ablation_matrix,
    shopping_task,
)
from mobilegui_ltm.profiles import MATRIX_MODES
from mobilegui_ltm.schema import MemoryKind


def test_each_matrix_mode_runs_and_full_beats_off(tmp_path):
    task = shopping_task()
    matrix = run_ablation_matrix(
        task,
        k=2,
        modes=MATRIX_MODES,
        store_factory=lambda mode: create_store(
            tmp_path / mode, agent_id="dummy", profile=mode
        ),
        agent_factory=DummyGUIAgent,
    )
    assert list(matrix.reports) == list(MATRIX_MODES)
    table = matrix.format_table()
    assert "recovery_after_failure" in table
    assert "pass@2" in table
    for mode in MATRIX_MODES:
        report = matrix.report(mode)
        assert len(report.attempts) >= 1

    off = matrix.report("off")
    full = matrix.report("full")
    assert off.success is False
    assert off.recovered_after_failure is False
    assert full.success is True
    assert full.recovered_after_failure is True
    assert float(full.success) > float(off.success)

    failures = matrix.report("failures-only")
    assert failures.success is True
    assert failures.recovered_after_failure is True

    shortcuts = matrix.report("shortcuts-only")
    assert shortcuts.success is False
    assert shortcuts.recovered_after_failure is False

    anchors = matrix.report("anchors")
    assert len(anchors.attempts) == 2
    assert anchors.success is True
    assert anchors.recovered_after_failure is True


def test_failures_only_profile_writes_only_failure_notes(tmp_path):
    store = create_store(tmp_path, agent_id="a", profile="failures-only")
    store.write_attempt(
        "t",
        1,
        traj=[{"app_id": "com.shop", "screen": "pdp", "observation": "ShopY oos size 9"}],
        outcome={"status": "failure", "reason": "Avoid ShopY; apply a size 9 filter"},
    )
    kinds = {r.kind for r in store.list_memories()}
    assert kinds == {MemoryKind.FAILURE_NOTE}


def test_profile_off_is_noop(tmp_path):
    store = create_store(tmp_path, agent_id="a", profile="off")
    assert store.enabled is False
    assert store.write_attempt("t", 1, traj="x", outcome=False) == []
    report = PassAtKRunner(store, DummyGUIAgent()).run(shopping_task(), k=2)
    assert report.success is False
