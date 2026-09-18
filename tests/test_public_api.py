from __future__ import annotations

import mobilegui_ltm as m


def test_public_exports():
    assert m.__version__ == "0.3.0"
    assert m.create_store.__doc__
    assert m.MemoryStore is not None
    assert m.MemoryKind.UI_FACT.value == "ui_fact"
    assert m.MemoryKind.SHORTCUT.value == "shortcut"
    assert m.MemoryKind.CAUSAL_ANCHOR.value == "causal_anchor"
    assert m.InjectionTarget.WORKER.value == "worker"
    assert m.JsonFileBackend is not None
    assert m.BM25Retriever is not None
    assert m.EmbeddingRetriever is not None
    assert m.HybridRetriever is not None
    assert m.FakeEmbedder is not None
    assert m.HashingEmbedder is not None
    assert m.ShortcutEncoder is not None
    assert m.CausalAnchorEncoder is not None
    assert m.ShortcutSpec is not None
    assert m.AtomicAction is not None
    assert m.RecordStatus.ACTIVE.value == "active"
    assert m.format_shortcut_for_worker is not None
    assert "full" in m.MATRIX_MODES
    assert callable(m.MemoryStore.remember)
    assert callable(m.MemoryStore.update)
    assert callable(m.MemoryStore.delete)
