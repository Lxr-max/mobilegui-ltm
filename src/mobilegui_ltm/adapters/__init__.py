"""Thin adapters for pass@k loops and MobileWorld / AndroidWorld-style envs."""

from mobilegui_ltm.adapters.agent_s2 import PlannerWorkerAdapter
from mobilegui_ltm.adapters.android_world import AndroidWorldAdapter
from mobilegui_ltm.adapters.dummy import DummyGUIAgent, shopping_task
from mobilegui_ltm.adapters.pass_at_k import (
    AblationReport,
    AttemptResult,
    MatrixReport,
    PassAtKReport,
    PassAtKRunner,
    run_ablation_matrix,
    run_ltm_ablation,
)

__all__ = [
    "AblationReport",
    "AndroidWorldAdapter",
    "AttemptResult",
    "DummyGUIAgent",
    "MatrixReport",
    "PassAtKReport",
    "PassAtKRunner",
    "PlannerWorkerAdapter",
    "run_ablation_matrix",
    "run_ltm_ablation",
    "shopping_task",
]
