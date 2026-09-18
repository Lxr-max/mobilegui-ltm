"""CLI: dummy pass@k demo with LTM on / off ablation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mobilegui_ltm.adapters.dummy import DummyGUIAgent, shopping_task
from mobilegui_ltm.adapters.pass_at_k import (
    AblationReport,
    PassAtKReport,
    PassAtKRunner,
    run_ltm_ablation,
)
from mobilegui_ltm.api import MemoryStore, create_store


def _store(data_dir: Path, *, enabled: bool, label: str) -> MemoryStore:
    return create_store(data_dir / label, agent_id="dummy-shopping", enabled=enabled)


def _print_report(report: PassAtKReport) -> None:
    mode = "on" if report.ltm_enabled else "off"
    print(f"LTM {mode}  task={report.task_id}  k={report.k}")
    for attempt in report.attempts:
        status = "SUCCESS" if attempt.success else "FAIL"
        retrieved = len(attempt.memories_retrieved)
        written = len(attempt.memories_written)
        print(
            f"  attempt {attempt.attempt_k}: {status}  "
            f"retrieved={retrieved} written={written}  "
            f"ltm_applied={attempt.ltm_applied}"
        )
        if attempt.outcome.reason:
            print(f"    reason: {attempt.outcome.reason}")
    solved = report.solved_at if report.solved_at is not None else "unsolved"
    print(f"  pass@{report.k}={int(report.success)}  solved_at={solved}  "
          f"recovery_after_failure={int(report.recovered_after_failure)}")


def run_demo(
    *,
    ltm: str = "ablate",
    k: int = 2,
    data_dir: Path | None = None,
) -> AblationReport | PassAtKReport:
    data_dir = data_dir or (Path.cwd() / "demo_store")
    data_dir.mkdir(parents=True, exist_ok=True)
    task = shopping_task()

    if ltm == "ablate":
        report = run_ltm_ablation(
            task,
            k=k,
            store_factory=lambda enabled: _store(
                data_dir, enabled=enabled, label="on" if enabled else "off"
            ),
            agent_factory=DummyGUIAgent,
        )
        print(report.format_table())
        print()
        print("--- ltm-off ---")
        _print_report(report.ltm_off)
        print("--- ltm-on ---")
        _print_report(report.ltm_on)
        return report

    enabled = ltm == "on"
    store = _store(data_dir, enabled=enabled, label=ltm)
    report = PassAtKRunner(store, DummyGUIAgent(), ltm_enabled=enabled).run(task, k=k)
    _print_report(report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mobilegui-ltm-demo",
        description=(
            "Dummy pass@k shopping task: attempt 1 fails, attempt 2 can recover "
            "when long-term memory is left on (no reset between attempts)."
        ),
    )
    parser.add_argument(
        "--ltm",
        choices=("on", "off", "ablate"),
        default="ablate",
        help="Enable LTM, disable it, or run both and print a comparison table.",
    )
    parser.add_argument(
        "--ablate",
        action="store_true",
        help="Shorthand for --ltm ablate (LTM on/off comparison table).",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=2,
        help="Maximum attempts (pass@k). Default: 2.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory for JSON memory files (default: ./demo_store).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.k < 1:
        print("error: --k must be >= 1", file=sys.stderr)
        return 2
    ltm = "ablate" if args.ablate else args.ltm
    run_demo(ltm=ltm, k=args.k, data_dir=args.data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
