"""Shared text helpers for trajectory encoders."""

from __future__ import annotations

from typing import Any, Iterable

from mobilegui_ltm.schema import Trajectory, TrajectoryStep


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(f"{key}: {as_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(as_text(item) for item in value)
    return str(value)


def step_text(step: TrajectoryStep) -> str:
    return " ".join(
        part
        for part in (
            as_text(step.observation),
            as_text(step.action),
            step.app_id or "",
            step.screen or "",
            as_text(step.extras),
        )
        if part
    )


def traj_text(traj: Trajectory) -> str:
    chunks = [as_text(traj.metadata)]
    for step in traj.steps:
        chunks.append(step_text(step))
    return " ".join(chunks)


def string_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def collect_app_ids(traj: Trajectory) -> list[str]:
    app_ids = list(traj.app_ids)
    for step in traj.steps:
        if step.app_id and step.app_id not in app_ids:
            app_ids.append(step.app_id)
    for extra in string_list(traj.metadata.get("app_ids") or traj.metadata.get("apps")):
        if extra not in app_ids:
            app_ids.append(extra)
    return app_ids


def action_text(step: TrajectoryStep) -> str:
    return as_text(step.action).strip()
