from __future__ import annotations

from mobilegui_ltm import create_store
from mobilegui_ltm.adapters import (
    AndroidWorldAdapter,
    DummyGUIAgent,
    PassAtKRunner,
    PlannerWorkerAdapter,
    run_ltm_ablation,
    shopping_task,
)
from mobilegui_ltm.inject import LTM_START
from mobilegui_ltm.schema import InjectionTarget, MemoryKind


def test_pass_at_k_ltm_on_recovers(tmp_path):
    store = create_store(tmp_path / "on", agent_id="dummy")
    report = PassAtKRunner(store, DummyGUIAgent()).run(shopping_task(), k=2)
    assert report.ltm_enabled is True
    assert len(report.attempts) == 2
    assert report.attempts[0].success is False
    assert report.attempts[0].memories_written
    assert report.attempts[1].memories_retrieved
    assert report.attempts[1].ltm_applied is True
    assert LTM_START in report.attempts[1].injected_prompt
    assert report.attempts[1].success is True
    assert report.recovered_after_failure is True
    # Memory was not reset: attempt-1 records still present after attempt 2.
    leftover = store.list_memories(task_id=shopping_task().task_id)
    assert any(r.attempt_k == 1 for r in leftover)
    assert any(r.attempt_k == 2 for r in leftover)


def test_pass_at_k_ltm_off_never_recovers(tmp_path):
    store = create_store(tmp_path / "off", agent_id="dummy", enabled=False)
    report = PassAtKRunner(store, DummyGUIAgent(), ltm_enabled=False).run(
        shopping_task(), k=2
    )
    assert len(report.attempts) == 2
    assert report.attempts[0].success is False
    assert report.attempts[1].success is False
    assert report.attempts[1].ltm_applied is False
    assert store.list_memories() == []


def test_ablation_table(tmp_path):
    task = shopping_task()

    def factory(enabled: bool):
        return create_store(
            tmp_path / ("on" if enabled else "off"),
            agent_id="dummy",
            enabled=enabled,
        )

    ablation = run_ltm_ablation(
        task, k=2, store_factory=factory, agent_factory=DummyGUIAgent
    )
    table = ablation.format_table()
    assert "ltm-off" in table and "ltm-on" in table
    assert "recovery_after_failure" in table
    assert ablation.ltm_off.success is False
    assert ablation.ltm_on.success is True
    rows = ablation.as_rows()
    assert rows[0]["pass@1"] == 0.0
    assert rows[1]["pass@2"] == 1.0


def test_android_world_adapter_hooks(tmp_path):
    store = create_store(tmp_path, agent_id="aw")
    adapter = AndroidWorldAdapter(store)
    prompt = adapter.before_attempt("t", "shop", "base prompt")
    assert prompt == "base prompt"  # nothing stored yet
    written = adapter.after_attempt(
        "t",
        1,
        traj=[{"app_id": "com.shop", "observation": "ShopY failed size 9"}],
        outcome={"status": "failure", "reason": "Avoid ShopY; prefer ShopX size 9 filter"},
    )
    assert written
    prompt2 = adapter.before_attempt("t", "ShopY ShopX size 9", "base prompt", app_ids=["com.shop"])
    assert LTM_START in prompt2


def test_planner_worker_split_inject(tmp_path):
    store = create_store(tmp_path, agent_id="s2")
    store.write_attempt(
        "t",
        1,
        traj={
            "steps": [{"app_id": "com.shop", "screen": "pdp", "observation": "stuck"}],
            "metadata": {
                "ui_facts": ["ShopX filter chip is top-right"],
                "subgoals": ["reached PDP, not cart"],
            },
        },
        outcome={"status": "failure", "reason": "Avoid ShopY on retry"},
    )
    adapter = PlannerWorkerAdapter(store)
    planner = adapter.inject_planner({"planner": "think"}, "ShopY PDP cart", task_id="t")
    worker = adapter.inject_worker({"worker": "act"}, "filter chip ShopX", task_id="t")
    assert LTM_START in planner["planner"]
    assert LTM_START in worker["worker"]
    planner_mems = adapter.memories_for_planner("Avoid ShopY", task_id="t")
    assert any(
        r.kind in {MemoryKind.FAILURE_NOTE, MemoryKind.SUBGOAL_TRACE} for r in planner_mems
    )
    # InjectionTarget used by the reference adapter
    assert InjectionTarget.PLANNER.value == "planner"
