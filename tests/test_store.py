from __future__ import annotations

from pathlib import Path

import pytest

from mobilegui_ltm import create_store
from mobilegui_ltm.schema import MemoryKind, OutcomeStatus
from mobilegui_ltm.store import JsonFileBackend, SQLiteBackend


def test_write_then_list_roundtrip(store):
    written = store.write_attempt(
        "task-a",
        1,
        traj=[{"app_id": "com.shop", "screen": "home", "action": "open", "observation": "home"}],
        outcome={"status": OutcomeStatus.FAILURE, "reason": "timed out on home"},
    )
    assert written
    listed = store.list_memories()
    assert {r.id for r in listed} == {r.id for r in written}
    path = Path(store.backend.path_for("agent-a"))
    assert path.exists()
    assert "timed out" in path.read_text(encoding="utf-8")


def test_write_success_and_failure(store):
    store.write_attempt("t", 1, traj="fail path", outcome=False)
    store.write_attempt("t", 2, traj="ok path", outcome=True)
    notes = store.list_memories(kinds=[MemoryKind.FAILURE_NOTE])
    traces = store.list_memories(kinds=[MemoryKind.SUBGOAL_TRACE])
    assert notes  # attempt 1
    assert traces  # both attempts
    assert any("attempt 2" in r.content.lower() or r.attempt_k == 2 for r in traces)


def test_sqlite_stub_raises():
    with pytest.raises(NotImplementedError, match="phase-2"):
        SQLiteBackend(":memory:")


def test_json_backend_replace_all(tmp_path):
    backend = JsonFileBackend(tmp_path)
    store = create_store(backend=backend, agent_id="x")
    store.write_attempt("t", 1, traj="a", outcome="failure")
    assert store.list_memories()
    backend.replace_all([])
    assert store.list_memories() == []
