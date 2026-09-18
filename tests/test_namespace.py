from __future__ import annotations

from mobilegui_ltm import create_store
from mobilegui_ltm.schema import MemoryKind


def test_namespace_isolation(tmp_path):
    root = tmp_path / "shared"
    a = create_store(root, agent_id="agent-a")
    b = a.namespace("agent-b")
    a.write_attempt(
        "task-shared",
        1,
        traj=[{"app_id": "com.shop", "observation": "alpha secret for A"}],
        outcome={"status": "failure", "reason": "alpha failed for agent A only"},
    )
    b.write_attempt(
        "task-shared",
        1,
        traj=[{"app_id": "com.shop", "observation": "beta secret for B"}],
        outcome={"status": "failure", "reason": "beta failed for agent B only"},
    )

    a_hits = a.retrieve("alpha beta secret", task_id="task-shared", k=10)
    b_hits = b.retrieve("alpha beta secret", task_id="task-shared", k=10)
    assert a_hits
    assert b_hits
    assert all(r.agent_id == "agent-a" for r in a_hits)
    assert all(r.agent_id == "agent-b" for r in b_hits)
    assert all("beta failed" not in r.content for r in a_hits)
    assert all("alpha failed" not in r.content for r in b_hits)


def test_clear_only_current_namespace(tmp_path):
    root = tmp_path / "shared"
    a = create_store(root, agent_id="agent-a")
    b = a.namespace("agent-b")
    a.write_attempt("t", 1, traj="a", outcome=False)
    b.write_attempt("t", 1, traj="b", outcome=False)
    removed = a.clear()
    assert removed > 0
    assert a.list_memories() == []
    assert b.list_memories()
    b.clear(all_agents=True)
    assert b.list_memories() == []
    assert a.list_memories() == []


def test_list_unranked_stays_namespaced(tmp_path):
    store = create_store(tmp_path, agent_id="solo")
    store.write_attempt("t", 1, traj="note", outcome=False)
    other = store.namespace("other")
    assert other.list_memories(kinds=[MemoryKind.FAILURE_NOTE]) == []
