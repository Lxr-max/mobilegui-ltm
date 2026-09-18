from __future__ import annotations

from mobilegui_ltm import create_store
from mobilegui_ltm.encode.anchors import CausalAnchorEncoder
from mobilegui_ltm.retrieve.scoring import expand_links
from mobilegui_ltm.schema import (
    AttemptOutcome,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    Trajectory,
    TrajectoryStep,
)


def _fail_traj() -> Trajectory:
    return Trajectory(
        app_ids=["com.shop"],
        steps=[
            TrajectoryStep(app_id="com.shop", screen="search", action="type:q", observation="hits"),
            TrajectoryStep(app_id="com.shop", screen="pdp", action="tap:ShopY", observation="oos"),
        ],
    )


def test_anchor_encoder_emits_transition_and_failure():
    recs = CausalAnchorEncoder().encode(
        "shop",
        1,
        _fail_traj(),
        AttemptOutcome(status=OutcomeStatus.FAILURE, reason="Avoid ShopY"),
        "a",
    )
    assert recs
    assert all(r.kind is MemoryKind.CAUSAL_ANCHOR for r in recs)
    assert any("search -> pdp" in r.content for r in recs)
    fail = next(r for r in recs if "failure" in r.tags)
    assert fail.metadata.get("evidence")
    assert fail.depends_on
    assert "ui:sellers" in fail.depends_on


def test_write_attempt_stores_anchors(store):
    written = store.write_attempt(
        "shop",
        1,
        _fail_traj(),
        {"status": "failure", "reason": "Avoid ShopY; apply size filter"},
    )
    assert any(r.kind is MemoryKind.CAUSAL_ANCHOR for r in written)


def test_expand_links_one_hop():
    seller = MemoryRecord(
        id="s1",
        kind=MemoryKind.UI_FACT,
        content="Seller ShopX",
        task_id="t",
        attempt_k=1,
        agent_id="a",
        logical_key="ui:sellers",
    )
    anchor = MemoryRecord(
        id="a1",
        kind=MemoryKind.CAUSAL_ANCHOR,
        content="Failure after tap ShopY",
        task_id="t",
        attempt_k=1,
        agent_id="a",
        depends_on=["ui:sellers"],
        metadata={"links": ["ui:sellers"], "evidence": {"screen": "pdp", "widget": "tap:ShopY"}},
    )
    expanded = expand_links([anchor], [anchor, seller], hops=1)
    assert [r.id for r in expanded] == ["a1", "s1"]
    assert expand_links([anchor], [anchor, seller], hops=0) == [anchor]


def test_store_retrieve_expand_hops(tmp_path):
    store = create_store(tmp_path, agent_id="a", expand_hops=1)
    store.remember("Seller ShopX", kind=MemoryKind.UI_FACT, key="ui:sellers", task_id="t")
    store.remember(
        "Failure after opening ShopY",
        kind=MemoryKind.CAUSAL_ANCHOR,
        key="anchor:fail",
        task_id="t",
        depends_on=["ui:sellers"],
        metadata={"links": ["ui:sellers"], "evidence": {"screen": "pdp"}},
    )
    hits = store.retrieve("Failure ShopY", task_id="t", k=1, expand_hops=1)
    kinds = {h.kind for h in hits}
    assert MemoryKind.CAUSAL_ANCHOR in kinds
    assert MemoryKind.UI_FACT in kinds
