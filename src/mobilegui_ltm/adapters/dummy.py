"""Rule-based stand-in agent for the pass@k demo and unit tests.

The shopping task instruction already names ShopX and size 9, but the agent
taps the first search hit (ShopY) unless injected failure notes from a prior
attempt tell it to avoid ShopY and apply a size filter. This isolates the
effect of cross-attempt memory from the original instruction.
"""

from __future__ import annotations

from mobilegui_ltm.inject import LTM_END, LTM_START
from mobilegui_ltm.schema import (
    AttemptOutcome,
    EvalTask,
    OutcomeStatus,
    Trajectory,
    TrajectoryStep,
)

SHOPPING_APP = "com.example.shopping"

SHOPPING_TASK = EvalTask(
    task_id="shop-red-sneakers-size9",
    instruction=(
        "Open the Shopping app and add red sneakers, size 9, sold by ShopX, "
        "to the cart."
    ),
    query="red sneakers size 9 ShopX cart Shopping",
    app_ids=[SHOPPING_APP],
)


def shopping_task() -> EvalTask:
    return SHOPPING_TASK.model_copy(deep=True)


def _memory_block(prompt: object) -> str:
    text = prompt if isinstance(prompt, str) else str(prompt)
    start = text.find(LTM_START)
    end = text.find(LTM_END)
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start : end + len(LTM_END)].lower()


def _recovered_from_memory(block: str) -> bool:
    if not block:
        return False
    avoided_wrong_seller = any(
        needle in block
        for needle in (
            "avoid shopy",
            "unavailable at shopy",
            "shopy does not",
            "prefer shopx",
        )
    )
    size_filter = any(
        needle in block
        for needle in (
            "size 9 filter",
            "apply a size 9",
            "filter before opening",
            "apply a size",
        )
    )
    return avoided_wrong_seller and size_filter


class DummyGUIAgent:
    """Deterministic GUI agent used to prove retrieve + inject across attempts."""

    def run_attempt(
        self,
        task: EvalTask,
        prompt: object,
        attempt_k: int,
    ) -> tuple[Trajectory, AttemptOutcome]:
        block = _memory_block(prompt)
        if _recovered_from_memory(block):
            return self._success_traj(task, attempt_k), AttemptOutcome(
                status=OutcomeStatus.SUCCESS,
                reason="Added ShopX red sneakers size 9 to cart.",
                metrics={"attempt": attempt_k},
            )
        return self._failure_traj(task, attempt_k), AttemptOutcome(
            status=OutcomeStatus.FAILURE,
            reason=(
                "Size 9 unavailable at ShopY. Avoid ShopY; the item is sold by ShopX. "
                "On retry, apply a size 9 filter before opening a product."
            ),
            metrics={"attempt": attempt_k},
        )

    def _failure_traj(self, task: EvalTask, attempt_k: int) -> Trajectory:
        app = (task.app_ids or [SHOPPING_APP])[0]
        return Trajectory(
            app_ids=[app],
            metadata={
                "ui_facts": [
                    "Search results list ShopY first and ShopX second.",
                    "ShopY product page exposes sizes 7-8 only.",
                ]
            },
            steps=[
                TrajectoryStep(
                    app_id=app,
                    screen="home",
                    action="open_app",
                    observation="Shopping home",
                ),
                TrajectoryStep(
                    app_id=app,
                    screen="search",
                    action="type:red sneakers",
                    observation="Results: ShopY red sneakers, ShopX red sneakers",
                ),
                TrajectoryStep(
                    app_id=app,
                    screen="pdp",
                    action="tap:ShopY red sneakers",
                    observation="ShopY product, sizes 7-8 only, size 9 unavailable at ShopY",
                ),
                TrajectoryStep(
                    app_id=app,
                    screen="pdp",
                    action="tap:add_to_cart",
                    observation="Failed: size 9 unavailable at ShopY",
                ),
            ],
        )

    def _success_traj(self, task: EvalTask, attempt_k: int) -> Trajectory:
        app = (task.app_ids or [SHOPPING_APP])[0]
        return Trajectory(
            app_ids=[app],
            metadata={
                "ui_facts": [
                    "ShopX carries red sneakers in size 9.",
                    "Size filter control is on the search results screen.",
                ],
                "subgoals": ["search -> filter size 9 -> ShopX PDP -> cart"],
            },
            steps=[
                TrajectoryStep(
                    app_id=app,
                    screen="home",
                    action="open_app",
                    observation="Shopping home",
                ),
                TrajectoryStep(
                    app_id=app,
                    screen="search",
                    action="type:red sneakers",
                    observation="Results: ShopY, ShopX",
                ),
                TrajectoryStep(
                    app_id=app,
                    screen="search",
                    action="apply_filter:size=9",
                    observation="Filtered results: ShopX red sneakers size 9",
                ),
                TrajectoryStep(
                    app_id=app,
                    screen="pdp",
                    action="tap:ShopX red sneakers",
                    observation="ShopX product, size 9 in stock",
                ),
                TrajectoryStep(
                    app_id=app,
                    screen="cart",
                    action="tap:add_to_cart",
                    observation="Added to cart",
                ),
            ],
        )
