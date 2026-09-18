from __future__ import annotations

from mobilegui_ltm.inject import LTM_END, LTM_START, PromptInjector, format_memories
from mobilegui_ltm.schema import InjectionTarget, MemoryKind, MemoryRecord


def _rec(kind: MemoryKind, content: str, *, rec_id: str = "abc") -> MemoryRecord:
    return MemoryRecord(
        id=rec_id,
        kind=kind,
        content=content,
        task_id="t1",
        attempt_k=1,
        agent_id="a",
        app_ids=["com.shop"],
    )


def test_format_memories_empty():
    assert format_memories([]) == ""


def test_inject_string_prepends_markers(store):
    recs = [
        _rec(MemoryKind.FAILURE_NOTE, "Avoid ShopY"),
        _rec(MemoryKind.UI_FACT, "ShopX in results", rec_id="def"),
    ]
    out = store.inject("Do the task.", recs)
    assert isinstance(out, str)
    assert out.index(LTM_START) < out.index("Avoid ShopY") < out.index(LTM_END)
    assert out.index(LTM_END) < out.index("Do the task.")
    assert "UI facts" in out


def test_inject_empty_returns_original(store):
    assert store.inject("raw", []) == "raw"


def test_inject_dict_planner_and_worker():
    injector = PromptInjector()
    recs = [_rec(MemoryKind.SUBGOAL_TRACE, "stuck on PDP")]
    state = {"planner": "plan now", "worker": "act now"}
    planned = injector.inject(state, recs, target=InjectionTarget.PLANNER)
    assert LTM_START in planned["planner"]
    assert planned["worker"] == "act now"
    assert planned["_ltm_target"] == "planner"
    assert planned["_ltm_memory_ids"] == ["abc"]

    worked = injector.inject({"actor_prompt": "go"}, recs, target="worker")
    assert LTM_START in worked["actor_prompt"]


def test_inject_disabled(tmp_path):
    from mobilegui_ltm import create_store

    store = create_store(tmp_path, enabled=False)
    recs = [_rec(MemoryKind.UI_FACT, "secret")]
    assert store.inject("x", recs) == "x"
