from __future__ import annotations

from mobilegui_ltm import create_store
from mobilegui_ltm.inject import LTM_START
from mobilegui_ltm.schema import MemoryKind


def test_remember_targets_named_block(store):
    store.remember("Avoid ShopY", kind=MemoryKind.FAILURE_NOTE, key="fail:y", task_id="t")
    store.remember(
        "Shortcut: tap cart",
        kind=MemoryKind.SHORTCUT,
        key="shortcut:cart",
        task_id="t",
        metadata={"actions": ["tap:add_to_cart"]},
    )
    store.remember("filter=size:9", kind=MemoryKind.UI_FACT, key="ui:filters", task_id="t")

    failures = store.list_memories(block="planner_failures")
    shortcuts = store.list_memories(block="worker_shortcuts")
    ui = store.list_memories(block="ui_state")
    assert all(r.kind is MemoryKind.FAILURE_NOTE for r in failures)
    assert all(r.kind is MemoryKind.SHORTCUT for r in shortcuts)
    assert all(r.kind is MemoryKind.UI_FACT for r in ui)
    assert store.list_memories(block="worker_shortcuts", kinds=[MemoryKind.FAILURE_NOTE]) == []


def test_inject_block_isolates_planner_from_worker(store):
    store.remember(
        "Avoid ShopY on retry",
        kind=MemoryKind.FAILURE_NOTE,
        key="fail:y",
        task_id="shop",
    )
    store.remember(
        "Shortcut: apply size then cart",
        kind=MemoryKind.SHORTCUT,
        key="shortcut:filter",
        task_id="shop",
        metadata={
            "shortcut": {
                "name": "filter_cart",
                "description": "size then cart",
                "preconditions": ["app=com.shop"],
                "arguments": {"size": 9},
                "atomic_actions": [{"type": "apply_filter", "args": {"size": 9}}],
            }
        },
    )
    mixed = store.list_memories(task_id="shop")
    planner = store.inject("plan", mixed, block="planner_failures")
    worker = store.inject("act", mixed, block="worker_shortcuts")
    assert LTM_START in planner and LTM_START in worker
    assert "Avoid ShopY" in planner
    assert "call filter_cart" not in planner
    assert "[block=planner_failures]" in planner
    assert "call filter_cart" in worker
    assert "Avoid ShopY" not in worker
    assert "[block=worker_shortcuts]" in worker


def test_inject_block_retrieve_roundtrip(store):
    store.remember("ShopX carries size 9", key="ui:seller", task_id="t")
    prompt = store.inject_block("open pdp", "ui_state", "ShopX size", task_id="t")
    assert LTM_START in prompt
    assert "ShopX" in prompt
    assert "Failure notes" not in prompt


def test_custom_block_registry(tmp_path):
    store = create_store(
        tmp_path,
        agent_id="a",
        blocks={"tips": (MemoryKind.FAILURE_NOTE, MemoryKind.CAUSAL_ANCHOR)},
    )
    rec = store.remember("tip: avoid ShopY", kind=MemoryKind.FAILURE_NOTE, block="tips")
    assert rec is not None
    assert rec.block == "tips"
    hits = store.retrieve("avoid ShopY", block="tips", k=4)
    assert hits
    assert all(h.block == "tips" for h in hits)
