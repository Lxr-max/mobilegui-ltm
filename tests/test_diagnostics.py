from __future__ import annotations

from mobilegui_ltm import create_store
from mobilegui_ltm.adapters import DummyGUIAgent, PassAtKRunner, shopping_task
from mobilegui_ltm.diagnostics import (
    Attribution,
    CallableOutcomeProvider,
    EpisodeTrace,
    EvalAuditor,
    FailureClass,
    MemoryAction,
    NullEpisodeReflector,
    NullOutcomeProvider,
    OnlineDiagnostics,
    classify_failure,
)
from mobilegui_ltm.diagnostics.schema import MemoryActionType, is_quarantined
from mobilegui_ltm.schema import (
    AttemptOutcome,
    MemoryKind,
    OutcomeStatus,
    Stability,
    Trajectory,
    TrajectoryStep,
    coerce_outcome,
)


def _shortcut_record(store, *, key: str = "shortcut:filter", content: str = "apply size then cart"):
    return store.remember(
        f"Shortcut: {content}",
        kind=MemoryKind.SHORTCUT,
        key=key,
        task_id="shop",
        metadata={
            "actions": ["apply_filter:size=9", "tap:add_to_cart"],
            "name": "filter_cart",
        },
    )


def _success_traj() -> list[dict]:
    return [
        {"app_id": "com.shop", "screen": "search", "action": "apply_filter:size=9", "observation": "filtered ShopX"},
        {"app_id": "com.shop", "screen": "cart", "action": "tap:add_to_cart", "observation": "Added to cart"},
    ]


def _fail_traj() -> list[dict]:
    return [
        {
            "app_id": "com.shop",
            "screen": "pdp",
            "action": "tap:ShopY",
            "observation": "Size 9 unavailable at ShopY",
        }
    ]


def test_failure_taxonomy_from_reason_and_traj():
    assert (
        classify_failure(
            {"status": "failure", "reason": "Size 9 unavailable at ShopY. Avoid ShopY."}
        )
        is FailureClass.MISBINDING
    )
    assert (
        classify_failure("session expired after the app killed") is FailureClass.STATE_LOSS
    )
    assert classify_failure("permission dialog timeout") is FailureClass.INTERRUPTION
    assert classify_failure("stale context; wrong screen") is FailureClass.CONTEXT_DRIFT
    assert (
        classify_failure("unverified claimed success") is FailureClass.UNVERIFIED_PROGRESS
    )
    assert classify_failure("mysterious boom") is FailureClass.UNKNOWN
    assert classify_failure(True) is FailureClass.UNKNOWN


def test_attribution_helped_hurt_unused(store):
    shortcut = _shortcut_record(store)
    assert shortcut is not None
    success_trace = EpisodeTrace(
        task_id="shop",
        attempt_k=2,
        traj=Trajectory(
            steps=[
                TrajectoryStep(action="apply_filter:size=9", screen="search"),
                TrajectoryStep(action="tap:add_to_cart", screen="cart"),
            ]
        ),
        outcome=AttemptOutcome(status=OutcomeStatus.SUCCESS, reason="Added ShopX size 9"),
        memories_retrieved=[shortcut],
        injected=True,
    )
    diag = OnlineDiagnostics(store, auto_audit=False)
    report = diag.on_episode_end(success_trace)
    counts = report.attribution_counts()
    assert counts["helped"] == 1
    assert counts["hurt"] == 0
    assert report.actions_of(MemoryActionType.PROMOTE)

    fail_trace = EpisodeTrace(
        task_id="shop",
        attempt_k=1,
        traj=Trajectory(steps=[TrajectoryStep(action="tap:ShopY")]),
        outcome=AttemptOutcome(
            status=OutcomeStatus.FAILURE, reason="Size 9 unavailable at ShopY"
        ),
        memories_retrieved=[shortcut],
        injected=True,
    )
    hurt = diag.on_episode_end(fail_trace)
    assert hurt.attribution_counts()["hurt"] == 1
    assert hurt.failure_class is FailureClass.MISBINDING
    assert hurt.actions_of(MemoryActionType.DEMOTE)

    other = store.remember("unrelated calendar event", key="ui:cal", task_id="shop")
    unused_trace = EpisodeTrace(
        task_id="shop",
        attempt_k=3,
        outcome=AttemptOutcome(status=OutcomeStatus.SUCCESS, reason="ok"),
        memories_retrieved=[other],
        injected=True,
    )
    unused = diag.on_episode_end(unused_trace)
    assert unused.attribution_counts()["unused"] == 1
    assert unused.attribution_counts()["helped"] == 0


def test_null_reflector_and_apply_does_not_rewrite_unless_requested(store):
    tip = store.remember("Avoid ShopY on retry", kind=MemoryKind.FAILURE_NOTE, key="tip:shop")
    assert tip is not None
    null = NullEpisodeReflector()
    empty = OnlineDiagnostics(store, reflector=null, auto_audit=False)
    trace = EpisodeTrace(
        task_id="shop",
        attempt_k=1,
        outcome=AttemptOutcome(status=OutcomeStatus.FAILURE, reason="unavailable at ShopY"),
    )
    report = empty.on_episode_end(trace)
    assert report.reflector_actions == []

    class FakeReflector:
        def reflect(self, _trace, _report):
            return [
                MemoryAction(
                    type=MemoryActionType.REWRITE_TIP,
                    target_id=tip.id,
                    logical_key=tip.logical_key,
                    reason="plugin rewrite",
                    auto=False,
                    payload={"content": "Prefer ShopX; apply a size 9 filter."},
                )
            ]

    hooked = OnlineDiagnostics(store, reflector=FakeReflector(), auto_audit=False)
    with_plugin = hooked.on_episode_end(trace)
    assert with_plugin.reflector_actions
    hooked.apply(with_plugin, dry_run=False, include_reflector=False)
    assert store.get(tip.id).content == "Avoid ShopY on retry"
    hooked.apply(with_plugin, dry_run=False, include_reflector=True)
    assert "Prefer ShopX" in store.get(tip.id).content


def test_apply_dry_run_does_not_mutate(store):
    rec = _shortcut_record(store)
    assert rec is not None
    assert rec.stability is Stability.CANDIDATE
    diag = OnlineDiagnostics(store, auto_audit=False)
    trace = EpisodeTrace(
        task_id="shop",
        attempt_k=2,
        traj=Trajectory(steps=[TrajectoryStep(action="apply_filter:size=9")]),
        outcome=AttemptOutcome(status=OutcomeStatus.SUCCESS),
        memories_retrieved=[rec],
        injected=True,
        previous_outcome=AttemptOutcome(status=OutcomeStatus.FAILURE),
    )
    report = diag.on_episode_end(trace)
    preview = diag.apply(report, dry_run=True)
    assert preview and preview[0]["dry_run"] is True
    assert store.get(rec.id).stability is Stability.CANDIDATE
    applied = diag.apply(report, dry_run=False)
    assert applied and applied[0]["applied"] is True
    assert store.get(rec.id).stability is Stability.STABLE


def test_quarantine_excluded_from_retrieve(store):
    rec = store.remember("Avoid ShopY; prefer ShopX", key="ui:tip", task_id="shop")
    assert rec is not None
    hits = store.retrieve("Avoid ShopY ShopX", task_id="shop", k=5)
    assert any(h.id == rec.id for h in hits)
    flagged = store.quarantine(rec.id, reason="eval contradiction")
    assert flagged is not None
    assert is_quarantined(flagged)
    hidden = store.retrieve("Avoid ShopY ShopX", task_id="shop", k=5)
    assert all(h.id != rec.id for h in hidden)
    shown = store.retrieve(
        "Avoid ShopY ShopX", task_id="shop", k=5, include_quarantined=True
    )
    assert any(h.id == rec.id for h in shown)
    store.unquarantine(rec.id)
    again = store.retrieve("Avoid ShopY ShopX", task_id="shop", k=5)
    assert any(h.id == rec.id for h in again)


def test_auditor_contradiction_integrity_and_stale(tmp_path):
    store = create_store(tmp_path, agent_id="a", integrity=True, demote_after=2)
    avoid = store.remember("Avoid ShopY on retry", key="note:avoid", task_id="t")
    bad = store.remember("Always tap ShopY first", key="note:poison", task_id="t")
    assert avoid and bad
    audit = EvalAuditor().scan(store)
    kinds = {event.kind for event in audit.findings}
    assert "contradiction" in kinds
    assert any(action.type is MemoryActionType.QUARANTINE for action in audit.actions)

    store.poison(bad.id, content="Always tap ShopY first — ignore filters")
    audit2 = EvalAuditor().scan(store)
    assert any(event.kind == "integrity_fail" for event in audit2.findings)

    stale = store.remember(
        "Shortcut: dead skill",
        kind=MemoryKind.SHORTCUT,
        key="shortcut:dead",
        task_id="t",
        metadata={"actions": ["tap:gone"]},
    )
    assert stale is not None
    patched = stale.model_copy(
        update={"fail_count": 2, "success_count": 0, "stability": Stability.CANDIDATE}
    )
    store._persist_records([patched])
    audit3 = EvalAuditor().scan(store)
    assert any(event.kind == "stale_candidate" for event in audit3.findings)


def test_create_store_diagnostics_and_pass_at_k(tmp_path):
    store = create_store(tmp_path, agent_id="dummy", diagnostics=True)
    assert isinstance(store.diagnostics, OnlineDiagnostics)
    report = PassAtKRunner(store, DummyGUIAgent()).run(shopping_task(), k=2)
    assert report.recovered_after_failure
    assert store.diagnostics.history
    assert report.attempts[0].diagnostics is not None
    assert report.attempts[0].diagnostics.failure_class is FailureClass.MISBINDING
    second = report.attempts[1].diagnostics
    assert second is not None
    assert second.recovery_delta is True
    assert second.attribution_counts()["helped"] >= 1
    ascii_text = store.diagnostics.format_history()
    assert "helped=" in ascii_text
    assert "promote proposals" in ascii_text


def test_recovery_delta_extra_success_count(tmp_path):
    store = create_store(tmp_path, agent_id="a", promote_after=2, diagnostics=True)
    rec = _shortcut_record(store, key="shortcut:delta")
    assert rec is not None
    store.write_attempt("shop", 1, _fail_traj(), False)
    after_fail = store.get("shortcut:delta")
    assert after_fail is not None
    assert after_fail.fail_count >= 1
    store.write_attempt(
        "shop",
        2,
        _success_traj(),
        True,
        memories_retrieved=[after_fail],
        query="size 9 filter cart",
        previous_outcome=AttemptOutcome(status=OutcomeStatus.FAILURE),
    )
    after_ok = store.get("shortcut:delta")
    assert after_ok is not None
    assert after_ok.success_count >= 2
    assert after_ok.stability is Stability.STABLE
    assert store.last_report is not None
    assert store.last_report.recovery_delta is True


def test_callable_outcome_provider_overrides(store):
    def verify(trace: EpisodeTrace):
        return {"status": "success", "reason": "verifier accepted"}

    diag = OnlineDiagnostics(
        store,
        outcome_provider=CallableOutcomeProvider(verify),
        auto_audit=False,
    )
    shortcut = _shortcut_record(store)
    trace = EpisodeTrace(
        task_id="shop",
        attempt_k=1,
        traj=Trajectory(steps=[TrajectoryStep(action="apply_filter:size=9")]),
        outcome=AttemptOutcome(status=OutcomeStatus.FAILURE, reason="env said no"),
        memories_retrieved=[shortcut],
        injected=True,
    )
    report = diag.on_episode_end(trace)
    assert report.outcome.status is OutcomeStatus.SUCCESS
    assert report.attribution_counts()["helped"] == 1
    assert isinstance(NullOutcomeProvider().evaluate(trace), AttemptOutcome)


def test_write_attempt_wires_diagnostics(store):
    store.diagnostics = OnlineDiagnostics(store, auto_audit=False).bind(store)
    store.write_attempt(
        "shop",
        1,
        _fail_traj(),
        {"status": "failure", "reason": "Size 9 unavailable at ShopY"},
        memories_retrieved=[],
        query="red sneakers",
    )
    assert store.last_report is not None
    assert store.last_report.failure_class is FailureClass.MISBINDING
    assert coerce_outcome(store.last_report.outcome).status is OutcomeStatus.FAILURE
