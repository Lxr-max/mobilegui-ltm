from __future__ import annotations

import pytest

from mobilegui_ltm.api import create_store


@pytest.fixture
def store(tmp_path):
    return create_store(tmp_path / "ltm", agent_id="agent-a")
