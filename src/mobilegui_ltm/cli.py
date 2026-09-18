"""CLI: dummy pass@k demo with LTM on / off, kind matrix, and diagnostics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mobilegui_ltm.adapters.dummy import DummyGUIAgent, shopping_task
from mobilegui_ltm.adapters.pass_at_k import (
    AblationReport,
    MatrixReport,
    PassAtKReport,
    PassAtKRunner,
    run_ablation_matrix,
    run_ltm_ablation,
)
from mobilegui_ltm.api import MemoryStore, create_store
from mobilegui_ltm.diagnostics.auditor import EvalAuditor
from mobilegui_ltm.profiles import MATRIX_MODES

_LTM_CHOICES = (
    "on",
    "off",
    "ablate",
    "full",
    "failures-only",
    "shortcuts-only",
    "anchors",
    "matrix",
    "diagnose",
)


def _store(
    data_dir: Path,
    *,
    enabled: bool = True,
    label: str,
    retriever: str = "bm25",
    profile: str | None = None,
    diagnostics: bool = False,
) -> MemoryStore:
    kwargs: dict = {}
    if retriever and retriever != "bm25":
        kwargs["retriever"] = retriever
    if profile is not None:
        kwargs["profile"] = profile
    if diagnostics:
        kwargs["diagnostics"] = True
    return create_store(
        data_dir / label, agent_id="dummy-shopping", enabled=enabled, **kwargs
    )


def _print_report(report: PassAtKReport, *, label: str | None = None) -> None:
    mode = label or ("on" if report.ltm_enabled else "off")
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


def _print_diagnostics(
    store: MemoryStore,
    *,
    apply_diagnostics: bool = False,
) -> None:
    diag = store.diagnostics
    dry_run = not apply_diagnostics
    if diag is None:
        print("OnlineDiagnostics  (disabled)")
        return
    if apply_diagnostics:
        diag.apply(dry_run=False, include_reflector=False)
    else:
        diag.apply(dry_run=True, include_reflector=False)
    print()
    print(diag.format_history(dry_run=dry_run))
    audit = EvalAuditor().scan(store)
    print(audit.format_summary())
    if audit.actions:
        for action in audit.actions:
            target = action.logical_key or action.target_id or "?"
            print(f"  {action.type.value} {target}  ({action.reason})")
    else:
        print("  (no additional quarantine suggestions)")


def run_demo(
    *,
    ltm: str = "ablate",
    k: int = 2,
    data_dir: Path | None = None,
    retriever: str = "bm25",
    apply_diagnostics: bool = False,
) -> AblationReport | PassAtKReport | MatrixReport:
    data_dir = data_dir or (Path.cwd() / "demo_store")
    data_dir.mkdir(parents=True, exist_ok=True)
    task = shopping_task()

    if ltm == "diagnose":
        store = _store(
            data_dir,
            enabled=True,
            label="diagnose",
            retriever=retriever,
            profile="full",
            diagnostics=True,
        )
        report = PassAtKRunner(store, DummyGUIAgent(), ltm_enabled=store.enabled).run(
            task, k=k
        )
        _print_report(report, label="diagnose")
        _print_diagnostics(store, apply_diagnostics=apply_diagnostics)
        return report

    if ltm == "matrix":
        report = run_ablation_matrix(
            task,
            k=k,
            modes=MATRIX_MODES,
            store_factory=lambda mode: _store(
                data_dir,
                label=mode.replace("/", "-"),
                retriever=retriever,
                profile=mode,
            ),
            agent_factory=DummyGUIAgent,
        )
        print(report.format_table())
        print()
        for name, arm in report.reports.items():
            print(f"--- {name} ---")
            _print_report(arm, label=name)
        return report

    if ltm == "ablate":
        report = run_ltm_ablation(
            task,
            k=k,
            store_factory=lambda enabled: _store(
                data_dir,
                enabled=enabled,
                label="on" if enabled else "off",
                retriever=retriever,
                profile="full" if enabled else "off",
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

    profile = "full" if ltm == "on" else ("off" if ltm == "off" else ltm)
    enabled = profile not in {"off", "ltm-off"}
    store = _store(
        data_dir,
        enabled=enabled,
        label=ltm,
        retriever=retriever,
        profile=profile,
    )
    report = PassAtKRunner(store, DummyGUIAgent(), ltm_enabled=store.enabled).run(
        task, k=k
    )
    _print_report(report, label=ltm)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mobilegui-ltm-demo",
        description=(
            "Dummy pass@k shopping task: attempt 1 fails, attempt 2 can recover "
            "when long-term memory is left on (no reset between attempts). "
            "Use --matrix for off / failures-only / shortcuts-only / anchors / full. "
            "Use --diagnose for OnlineDiagnostics (attribution, promote/demote, auditor)."
        ),
    )
    parser.add_argument(
        "--ltm",
        choices=_LTM_CHOICES,
        default="ablate",
        help=(
            "Enable LTM, disable it, compare on/off, run one kind profile, "
            "run the full ablation matrix, or print online diagnostics."
        ),
    )
    parser.add_argument(
        "--ablate",
        action="store_true",
        help="Shorthand for --ltm ablate (LTM on/off comparison table).",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help=(
            "Shorthand for --ltm matrix (off, failures-only, shortcuts-only, "
            "anchors, full)."
        ),
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help=(
            "Shorthand for --ltm diagnose: dummy shopping task plus ASCII "
            "attribution / promote / demote / auditor summary (dry-run apply)."
        ),
    )
    parser.add_argument(
        "--apply-diagnostics",
        action="store_true",
        help=(
            "With --diagnose, apply conservative promote/demote/quarantine. "
            "Default is dry-run. Reflector rewrites are never auto-applied."
        ),
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
    parser.add_argument(
        "--retriever",
        choices=("bm25", "hybrid", "vector"),
        default="bm25",
        help="Ranking backend. 'hybrid' uses local hashing vectors (no downloads).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.k < 1:
        print("error: --k must be >= 1", file=sys.stderr)
        return 2
    ltm = args.ltm
    if args.diagnose:
        ltm = "diagnose"
    elif args.matrix:
        ltm = "matrix"
    elif args.ablate:
        ltm = "ablate"
    run_demo(
        ltm=ltm,
        k=args.k,
        data_dir=args.data_dir,
        retriever=args.retriever,
        apply_diagnostics=args.apply_diagnostics,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
