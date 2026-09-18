"""Sidecar vector index next to the JSON memory store.

Embeddings also live on ``MemoryRecord.embedding`` (export/import compatible).
The sidecar is a compact ``{agent}.vectors.json`` used to rebuild or inspect
the index without scanning every memory field.

Rebuild::

    store = create_store(path, retriever="hybrid")
    store.rebuild_index()
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Sequence

from mobilegui_ltm.schema import MemoryRecord
from mobilegui_ltm.store.json import sanitize_agent_id

SIDECAR_SUFFIX = ".vectors.json"


def sidecar_path(root: str | Path, agent_id: str) -> Path:
    return Path(root) / f"{sanitize_agent_id(agent_id)}{SIDECAR_SUFFIX}"


def is_sidecar_path(path: str | Path) -> bool:
    return Path(path).name.endswith(SIDECAR_SUFFIX)


class VectorSidecar:
    """Read/write ``{agent}.vectors.json`` under a store root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, agent_id: str) -> Path:
        return sidecar_path(self.root, agent_id)

    def write(
        self,
        agent_id: str,
        records: Sequence[MemoryRecord],
        *,
        embedder_name: str,
        dim: int,
    ) -> Path:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "agent_id": agent_id,
            "embedder": embedder_name,
            "dim": dim,
            "vectors": {
                record.id: record.embedding
                for record in records
                if record.embedding
            },
        }
        path = self.path_for(agent_id)
        text = json.dumps(payload, indent=2)
        fd, tmp_name = tempfile.mkstemp(prefix=".ltm-vec-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return path

    def read(self, agent_id: str) -> dict[str, Any] | None:
        path = self.path_for(agent_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def apply(self, agent_id: str, records: Sequence[MemoryRecord]) -> list[MemoryRecord]:
        """Fill missing ``record.embedding`` from the sidecar when dims match."""
        payload = self.read(agent_id)
        if not payload:
            return list(records)
        vectors = payload.get("vectors") or {}
        filled: list[MemoryRecord] = []
        for record in records:
            if record.embedding or record.id not in vectors:
                filled.append(record)
                continue
            vec = vectors[record.id]
            if isinstance(vec, list) and vec:
                filled.append(record.model_copy(update={"embedding": [float(x) for x in vec]}))
            else:
                filled.append(record)
        return filled

    def delete(self, agent_id: str | None = None) -> None:
        if agent_id is None:
            for path in list(self.root.glob(f"*{SIDECAR_SUFFIX}")):
                path.unlink(missing_ok=True)
            return
        self.path_for(agent_id).unlink(missing_ok=True)
