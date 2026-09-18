"""pass@k multi-attempt protocol: memory MUST NOT reset between attempts.

The environment (emulator / activity) may reset; this adapter never calls
``MemoryStore.clear()`` inside the attempt loop when LTM is enabled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from mobilegui_ltm.api import MemoryStore
from mobilegui_ltm.inject import LTM_START
from mobilegui_ltm.schema import (
    AttemptOutcome,
    EvalTask,
    InjectionTarget,
    MemoryRecord,
    OutcomeStatus,
    coerce_outcome,
)


class MobileGUIAgent(Protocol):
    """Minimal agent hook used by :class:`PassAtKRunner`."""

    def run_attempt(
        self,
        task: EvalTask,
        prompt: Any,
        attempt_k: int,
    ) -> tuple[Any, Any]:
        """Return ``(trajectory, outcome)`` for one attempt."""
        ...


@dataclass
class AttemptResult:
    attempt_k: int
    success: bool
    outcome: AttemptOutcome
    memories_retrieved: list[MemoryRecord] = field(default_factory=list)
    memories_written: list[MemoryRecord] = field(default_factory=list)
    injected_prompt: Any = None
    ltm_applied: bool = False


@dataclass
class PassAtKReport:
    task_id: str
    k: int
    ltm_enabled: bool
    attempts: list[AttemptResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return any(item.success for item in self.attempts)

    @property
    def solved_at(self) -> int | None:
        for item in self.attempts:
            if item.success:
                return item.attempt_k
        return None

    @property
    def recovered_after_failure(self) -> bool:
        if len(self.attempts) < 2:
            return False
        first_fail = self.attempts[0].success is False
        later_ok = any(item.success for item in self.attempts[1:])
        return first_fail and later_ok

    def pass_at(self, n: int) -> bool:
        return any(item.success and item.attempt_k <= n for item in self.attempts)


@dataclass
class AblationReport:
    """Side-by-side LTM on vs off for one task (citation-style table)."""

    task_id: str
    k: int
    ltm_off: PassAtKReport
    ltm_on: PassAtKReport

    def as_rows(self) -> list[dict[str, Any]]:
        def row(label: str, report: PassAtKReport) -> dict[str, Any]:
            return {
                "protocol": label,
                "pass@1": float(report.pass_at(1)),
                f"pass@{self.k}": float(report.success),
                "recovery_after_failure": float(report.recovered_after_failure),
                "solved_at": report.solved_at,
            }

        return [
            row("ltm-off", self.ltm_off),
            row("ltm-on", self.ltm_on),
        ]

    def format_table(self) -> str:
        rows = self.as_rows()
        pass_k = f"pass@{self.k}"
        headers = ["protocol", "pass@1", pass_k, "recovery_after_failure", "solved_at"]
        widths = {h: max(len(h), *(len(str(r[h])) for r in rows)) for h in headers}
        def fmt(row: dict[str, Any]) -> str:
            return " | ".join(str(row[h]).ljust(widths[h]) for h in headers)

        header_line = " | ".join(h.ljust(widths[h]) for h in headers)
        rule = "-+-".join("-" * widths[h] for h in headers)
        body = "\n".join(fmt(r) for r in rows)
        return f"{header_line}\n{rule}\n{body}"


class PassAtKRunner:
    """Run up to ``k`` attempts, persisting memory across failures when enabled."""

    def __init__(
        self,
        store: MemoryStore,
        agent: MobileGUIAgent,
        *,
        ltm_enabled: bool | None = None,
        retrieve_k: int = 8,
        target: InjectionTarget | str = InjectionTarget.SYSTEM,
    ) -> None:
        self.store = store
        self.agent = agent
        self.ltm_enabled = store.enabled if ltm_enabled is None else ltm_enabled
        self.retrieve_k = retrieve_k
        self.target = target

    def run(self, task: EvalTask, k: int = 2) -> PassAtKReport:
        if k < 1:
            raise ValueError("k must be >= 1")
        report = PassAtKReport(task_id=task.task_id, k=k, ltm_enabled=self.ltm_enabled)
        for attempt_k in range(1, k + 1):
            memories: list[MemoryRecord] = []
            prompt: Any = task.instruction
            if self.ltm_enabled:
                memories = self.store.retrieve(
                    task.retrieval_query(),
                    task_id=task.task_id,
                    app_ids=task.app_ids or None,
                    k=self.retrieve_k,
                )
                prompt = self.store.inject(task.instruction, memories, target=self.target)
            traj, raw_outcome = self.agent.run_attempt(task, prompt, attempt_k)
            outcome = coerce_outcome(raw_outcome)
            written: list[MemoryRecord] = []
            if self.ltm_enabled:
                written = self.store.write_attempt(
                    task.task_id, attempt_k, traj, outcome
                )
            success = outcome.status == OutcomeStatus.SUCCESS
            injected_text = prompt if isinstance(prompt, str) else str(prompt)
            report.attempts.append(
                AttemptResult(
                    attempt_k=attempt_k,
                    success=success,
                    outcome=outcome,
                    memories_retrieved=list(memories),
                    memories_written=list(written),
                    injected_prompt=prompt,
                    ltm_applied=bool(self.ltm_enabled and LTM_START in injected_text),
                )
            )
            if success:
                break
        return report


def run_ltm_ablation(
    task: EvalTask,
    *,
    k: int = 2,
    store_factory: Callable[[bool], MemoryStore],
    agent_factory: Callable[[], MobileGUIAgent],
    retrieve_k: int = 8,
) -> AblationReport:
    """Run the same task with LTM off and on (independent stores / agents)."""
    off_store = store_factory(False)
    off_store.enabled = False
    on_store = store_factory(True)
    on_store.enabled = True
    off_report = PassAtKRunner(
        off_store, agent_factory(), ltm_enabled=False, retrieve_k=retrieve_k
    ).run(task, k=k)
    on_report = PassAtKRunner(
        on_store, agent_factory(), ltm_enabled=True, retrieve_k=retrieve_k
    ).run(task, k=k)
    return AblationReport(task_id=task.task_id, k=k, ltm_off=off_report, ltm_on=on_report)
