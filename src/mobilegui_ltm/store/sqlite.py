"""SQLite backend — phase-2 stub.

The MVP persists with :class:`~mobilegui_ltm.store.json.JsonFileBackend` only.
This class exists so callers can swap backends later without renaming imports.
"""

from __future__ import annotations

from pathlib import Path


class SQLiteBackend:
    """Placeholder for a future SQLite / FTS backend."""

    def __init__(self, path: str | Path, **_kwargs: object) -> None:
        raise NotImplementedError(
            "SQLiteBackend is a phase-2 extension point. "
            "Use mobilegui_ltm.store.JsonFileBackend for the MVP."
        )
