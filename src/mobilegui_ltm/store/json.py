"""JSON-file backend (MVP default).

Layout::

    {root}/{sanitized_agent_id}.json

Each file is a versioned list of ``MemoryRecord`` objects. Writes are atomic
(temp file + ``os.replace``). No graph database is used.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Sequence

from mobilegui_ltm.schema import SCHEMA_VERSION, MemoryKind, MemoryRecord

_SAFE_AGENT = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_agent_id(agent_id: str) -> str:
    cleaned = _SAFE_AGENT.sub("_", agent_id.strip()) or "default"
    return cleaned[:128]


class JsonFileBackend:
    """Persist memories as one JSON document per agent namespace."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, agent_id: str) -> Path:
        return self.root / f"{sanitize_agent_id(agent_id)}.json"

    def upsert(self, records: Sequence[MemoryRecord]) -> None:
        by_agent: dict[str, list[MemoryRecord]] = {}
        for record in records:
            by_agent.setdefault(record.agent_id, []).append(record)
        for agent_id, batch in by_agent.items():
            existing = {item.id: item for item in self._read(agent_id)}
            for record in batch:
                existing[record.id] = record
            self._write(agent_id, list(existing.values()))

    def list(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        app_ids: Sequence[str] | None = None,
        kinds: Sequence[MemoryKind] | None = None,
    ) -> list[MemoryRecord]:
        if agent_id is None:
            records: list[MemoryRecord] = []
            for path in sorted(self.root.glob("*.json")):
                if path.name.endswith(".vectors.json"):
                    continue
                records.extend(self._read_path(path))
        else:
            records = self._read(agent_id)

        app_set = set(app_ids) if app_ids else None
        kind_set = set(kinds) if kinds else None
        out: list[MemoryRecord] = []
        for record in records:
            if task_id is not None and record.task_id != task_id:
                continue
            if kind_set is not None and record.kind not in kind_set:
                continue
            if app_set is not None:
                if record.app_ids and app_set.isdisjoint(record.app_ids):
                    continue
            out.append(record)
        return out

    def delete(self, *, agent_id: str | None = None) -> int:
        if agent_id is None:
            removed = 0
            for path in list(self.root.glob("*.json")):
                if path.name.endswith(".vectors.json"):
                    path.unlink(missing_ok=True)
                    continue
                removed += len(self._read_path(path))
                path.unlink(missing_ok=True)
            return removed
        records = self._read(agent_id)
        path = self.path_for(agent_id)
        path.unlink(missing_ok=True)
        sidecar = self.root / f"{sanitize_agent_id(agent_id)}.vectors.json"
        sidecar.unlink(missing_ok=True)
        return len(records)

    def remove(self, ids: Sequence[str], *, agent_id: str) -> int:
        """Hard-delete records by id in one namespace."""
        existing = self._read(agent_id)
        drop = set(ids)
        keep = [record for record in existing if record.id not in drop]
        self._write(agent_id, keep)
        return len(existing) - len(keep)

    def replace_all(self, records: Sequence[MemoryRecord]) -> None:
        self.delete()
        if records:
            self.upsert(records)

    def _read(self, agent_id: str) -> list[MemoryRecord]:
        return self._read_path(self.path_for(agent_id))

    def _read_path(self, path: Path) -> list[MemoryRecord]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        raw = payload.get("memories", payload if isinstance(payload, list) else [])
        return [MemoryRecord.model_validate(item) for item in raw]

    def _write(self, agent_id: str, records: list[MemoryRecord]) -> None:
        path = self.path_for(agent_id)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "agent_id": agent_id,
            "memories": [r.model_dump(mode="json") for r in records],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        fd, tmp_name = tempfile.mkstemp(prefix=".ltm-", suffix=".json", dir=self.root)
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
