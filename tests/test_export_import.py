from __future__ import annotations

from mobilegui_ltm import SessionBundle, create_store


def test_export_import_roundtrip_file(tmp_path):
    src = create_store(tmp_path / "src", agent_id="agent-a")
    src.write_attempt(
        "shop",
        1,
        traj=[{"app_id": "com.shop", "observation": "ShopY out of stock size 9"}],
        outcome={"status": "failure", "reason": "Avoid ShopY; prefer ShopX"},
    )
    bundle_path = tmp_path / "session.json"
    bundle = src.export_session(bundle_path)
    assert bundle_path.exists()
    assert isinstance(bundle, SessionBundle)
    assert bundle.agent_id == "agent-a"
    assert bundle.memories

    dest = create_store(tmp_path / "dest", agent_id="agent-a")
    n = dest.import_session(bundle_path)
    assert n == len(bundle.memories)
    hits = dest.retrieve("ShopY ShopX", task_id="shop", k=5)
    assert hits
    assert any("ShopY" in r.content for r in hits)


def test_import_remaps_namespace(tmp_path):
    src = create_store(tmp_path / "src", agent_id="old")
    src.write_attempt("t", 1, traj="hello-namespace-remap", outcome=False)
    dest = create_store(tmp_path / "dest", agent_id="new")
    dest.import_session(src.export_session(), into_current_namespace=True)
    assert dest.list_memories()
    assert all(r.agent_id == "new" for r in dest.list_memories())
