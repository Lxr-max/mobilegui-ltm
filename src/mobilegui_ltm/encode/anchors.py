"""Causal anchors: cheap 1-hop links, not a graph database.

Anchors capture *why* a subgoal moved or a failure happened, with evidence
(app / screen / widget) and ``depends_on`` pointers (logical keys or ids).
"""

from __future__ import annotations

from mobilegui_ltm.encode.text import as_text, collect_app_ids
from mobilegui_ltm.schema import (
    AttemptOutcome,
    Evidence,
    MemoryKind,
    MemoryRecord,
    OutcomeStatus,
    Trajectory,
)


class CausalAnchorEncoder:
    """Emit anchors at failure points and screen-to-screen transitions."""

    def encode(
        self,
        task_id: str,
        attempt_k: int,
        traj: Trajectory,
        outcome: AttemptOutcome,
        agent_id: str,
    ) -> list[MemoryRecord]:
        app_ids = collect_app_ids(traj)
        common = dict(
            task_id=task_id,
            attempt_k=attempt_k,
            agent_id=agent_id,
            app_ids=app_ids,
        )
        records: list[MemoryRecord] = []
        screens = [step.screen for step in traj.steps if step.screen]
        for prev, cur in zip(screens, screens[1:]):
            if prev == cur:
                continue
            records.append(
                MemoryRecord(
                    kind=MemoryKind.CAUSAL_ANCHOR,
                    content=f"Subgoal moved {prev} -> {cur}",
                    logical_key=f"{task_id}::anchor:transition:{prev}->{cur}",
                    screen=cur,
                    depends_on=["ui:screens", f"{task_id}::subgoal:progress"],
                    tags=["anchor", "transition", prev, cur],
                    metadata={
                        "evidence": Evidence(
                            app_id=app_ids[0] if app_ids else None,
                            screen=cur,
                            widget=None,
                        ).model_dump(),
                        "links": ["ui:screens", f"{task_id}::subgoal:progress"],
                    },
                    **common,
                )
            )

        if outcome.status != OutcomeStatus.SUCCESS:
            last = traj.steps[-1] if traj.steps else None
            screen = last.screen if last else None
            widget = as_text(last.action) if last else None
            reason = outcome.reason or (as_text(last.observation) if last else "failed")
            records.append(
                MemoryRecord(
                    kind=MemoryKind.CAUSAL_ANCHOR,
                    content=f"Failure at screen={screen or 'unknown'} after {widget or 'unknown'}: {reason}",
                    logical_key=f"{task_id}::anchor:failure",
                    screen=screen,
                    depends_on=["ui:sellers", "ui:filters", f"{task_id}::subgoal:progress"],
                    tags=["anchor", "failure"],
                    metadata={
                        "evidence": Evidence(
                            app_id=(last.app_id if last else None) or (app_ids[0] if app_ids else None),
                            screen=screen,
                            widget=widget,
                        ).model_dump(),
                        "links": ["ui:sellers", "ui:filters", f"{task_id}::subgoal:progress"],
                    },
                    **common,
                )
            )
        return records
