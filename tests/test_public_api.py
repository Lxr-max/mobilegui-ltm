from __future__ import annotations

import mobilegui_ltm as m


def test_public_exports():
    assert m.__version__ == "0.2.0"
    assert m.create_store.__doc__
    assert m.MemoryStore is not None
    assert m.MemoryKind.UI_FACT.value == "ui_fact"
    assert m.MemoryKind.SHORTCUT.value == "shortcut"
    assert m.InjectionTarget.WORKER.value == "worker"
    assert m.JsonFileBackend is not None
    assert m.BM25Retriever is not None
    assert m.EmbeddingRetriever is not None
    assert m.HybridRetriever is not None
    assert m.FakeEmbedder is not None
    assert m.HashingEmbedder is not None
    assert m.ShortcutEncoder is not None
