#!/usr/bin/env python3
"""Run the dummy pass@k shopping demo (LTM on / off / ablate)."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python examples/pass_at_k_demo/run_demo.py` from a checkout
# before (or without) an editable install by putting src/ on sys.path.
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from mobilegui_ltm.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
