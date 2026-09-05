"""Unit tests: policy scoring + decisions under Phase 1.6 semantics (handoff §24).

Successful mutation ≠ material progress: only a Hermes-confirmed landed file
mutation decays, and novelty (changed result/query) never decays by itself.
Steps-since-material-progress amplifies detector evidence but never fires
recovery on its own.
"""

from hermes_plugins.progress_guard import detectors, policy
from hermes_plugins.progress_guard.config import ProgressGuardConfig


def _cfg(**overrides):
    return ProgressGuardConfig.from_mapping(overrides)


def _score(events, cfg=None, event=None, prev=None, steps=None):
    cfg = cfg or _cfg()
    signals = detectors.evaluate(events, cfg)
    return policy.score_delta(
        signals, cfg, event=event, prev_result_fingerprint=prev,
        steps_since_material_progress=steps,
    )


def test_legit_polling_never_accumulates(ev):
    # poll -> 10, poll -> 30, poll -> 70, poll -> done  (handoff §15)
    cfg = _cfg()
    score = 0
    prev = None
    for pct in ["10", "30", "70", "done"]:
        event = ev("job_poll", {"id": "j1"}, pct, is_poll=True)
        signals = detectors.evaluate([event], cfg)
        delta = policy.score_delta(signals, cfg, event=event, prev_result_fingerprint=prev)
        score = max(0, score + delta)
        prev = event.result_fingerprint
    assert score == 0  # never triggers recovery


def test_landed_mutation_decays_score(ev):
    # patch Hermes confirmed landed (success: true) -> strong material decay
    cfg = _cfg()
    event = ev(
        "patch", {"f": "a"}, '{"success": true}',
        is_mutation=True, mutation_landed=True, material=True,
    )
    signals = detectors.evaluate([event], cfg)
    delta = policy.score_delta(signals, cfg, event=event, prev_result_fingerprint=None)
    assert delta == -3  # floor handled by caller clamp


def test_mutation_without_landed_does_not_decay(ev):
    # mutation "ok" with no landed proof is NOT material progress (handoff §4)
    cfg = _cfg()
    event = ev("patch", {"f": "a"}, "applied", is_mutation=True)
    signals = detectors.evaluate([event], cfg)
    delta = policy.score_delta(signals, cfg, event=event, prev_result_fingerprint=None)
    assert delta == 0


def test_novel_result_does_not_decay(ev):
    # changed result / different output is novelty, not progress (handoff §10)
    cfg = _cfg()
    a = ev("search", {"q": "x"}, "r1")
    b = ev("search", {"q": "x"}, "r2")
    signals = detectors.evaluate([a, b], cfg)
    delta = policy.score_delta(signals, cfg, event=b, prev_result_fingerprint=a.result_fingerprint)
    assert delta == 0


def test_material_progress_decays_even_without_mutation(ev):
    # a poll that visibly advanced is material progress (-2 decay) (handoff §15)
    cfg = _cfg()
    event = ev(
        "job_poll", {"id": "j1"}, "40%",
        is_poll=True, material=True, poll_pct=40,
    )
    signals = detectors.evaluate([event], cfg)
    delta = policy.score_delta(signals, cfg, event=event, prev_result_fingerprint=None)
    assert delta == -2


def test_novelty_without_progress_never_accumulates(ev):
    # 4 distinct searches with distinct results: no detector fires, no decay,
    # score stays 0 -> CONTINUE (handoff §24 "novelty-without-progress")
    cfg = _cfg()
    score = 0
    prev = None
    for q in ["alpha", "beta", "gamma", "delta"]:
        event = ev("search", {"query": q}, f"result-{q}")
        signals = detectors.evaluate([event], cfg)
        delta = policy.score_delta(signals, cfg, event=event, prev_result_fingerprint=prev)
        score = max(0, score + delta)
        prev = event.result_fingerprint
    assert score == 0
    assert policy.decide(score, cfg) == "CONTINUE"


def test_steps_bonus_amplifies_detector_evidence(ev):
    # persistent identical-result stagnation with many steps since material
    # progress earns a small bonus, but only when a detector already fired.
    cfg = _cfg()
    events = []
    for q in ["A", "B", "C", "D"]:
        events.append(ev("search", {"query": q}, "same-result"))
    signals = detectors.evaluate(events, cfg)
    # identical_result magnitude 4 >= threshold 3, no cycle active
    assert signals["identical_result"] >= cfg.identical_result.threshold
    assert signals["cycle"] is False and signals["family_cycle"] == 0
    base = policy.score_delta(signals, cfg, event=events[-1],
                              steps_since_material_progress=2)
    boosted = policy.score_delta(signals, cfg, event=events[-1],
                                 steps_since_material_progress=10)
    assert boosted == base + cfg.steps.bonus_delta


def test_steps_bonus_never_skips_cycle_recovery(ev):
    # a structural A/B cycle must not be pushed past RECOVER by the steps bonus
    cfg = _cfg()
    events = []
    for i in range(4):
        events.append(ev("A", {"i": i}, f"r{i}a"))
        events.append(ev("B", {"i": i}, f"r{i}b"))
    signals = detectors.evaluate(events, cfg)
    assert signals["cycle"] is True
    with_steps = policy.score_delta(
        signals, cfg, event=events[-1], steps_since_material_progress=99
    )
    without = policy.score_delta(signals, cfg, event=events[-1])
    assert with_steps == without  # bonus suppressed while a cycle escalates


def test_exact_repeat_escalates_to_block(ev):
    cfg = _cfg()
    events, score, prev = [], 0, None
    for _ in range(5):
        event = ev("tool", {"x": 1}, "r")
        events = list(events) + [event]
        delta = _score(events, cfg, event, prev)
        score = max(0, score + delta)
        prev = event.result_fingerprint
    assert score >= cfg.policy.block_score


def test_decide_thresholds():
    cfg = _cfg()
    assert policy.decide(0, cfg) == "CONTINUE"
    assert policy.decide(2, cfg) == "CONTINUE"
    assert policy.decide(3, cfg) == "WARN"
    assert policy.decide(4, cfg) == "WARN"
    assert policy.decide(5, cfg) == "RECOVER"
    assert policy.decide(6, cfg) == "RECOVER"
    assert policy.decide(7, cfg) == "BLOCK"
    assert policy.decide(12, cfg) == "BLOCK"


def test_identical_result_stagnation_signal(ev):
    # search(A) -> X, search(B) -> X, search(C) -> X (handoff §20)
    cfg = _cfg()
    events, score, prev = [], 0, None
    for q in ["A", "B", "C", "D", "E"]:
        event = ev("search", {"query": q}, "same-result")
        events = list(events) + [event]
        delta = _score(events, cfg, event, prev)
        score = max(0, score + delta)
        prev = event.result_fingerprint
    assert score >= cfg.policy.recover_score


def test_repeated_failure_detects_and_scores(ev):
    cfg = _cfg()
    events, score = [], 0
    for i in range(5):
        event = ev(
            "patch", {"f": f"file{i}"}, status="error",
            error_type="PatchError", error_message="context mismatch",
        )
        events = list(events) + [event]
        delta = _score(events, cfg, event, None)
        score = max(0, score + delta)
    assert score >= cfg.policy.warn_score
    assert score >= cfg.policy.recover_score


def test_disabled_detectors_produce_zero(ev):
    cfg = _cfg(
        exact_repeat={"enabled": False},
        identical_result={"enabled": False},
        cycle={"enabled": False},
        family_cycle={"enabled": False},
        repeated_failure={"enabled": False},
    )
    events = [
        ev("A", {"x": 1}, "r"), ev("A", {"x": 1}, "r"), ev("A", {"x": 1}, "r"),
    ]
    assert _score(events, cfg) == 0


def test_same_failure_after_mutation_scores_conservatively(ev):
    cfg = _cfg(repeated_failure={"enabled": False})
    first = [
        ev("terminal", {}, status="error", error_type="tool_error", error_message="AssertionError"),
        ev("patch", {}, '{"success": true}', is_mutation=True, mutation_landed=True),
        ev("terminal", {}, status="error", error_type="tool_error", error_message="AssertionError"),
    ]
    second = first + [
        ev("terminal", {}, status="error", error_type="tool_error", error_message="AssertionError"),
    ]
    assert _score(first, cfg, first[-1]) == 2
    assert _score(second, cfg, second[-1]) == 3


def test_session_trajectory_only_amplifies_active_signal(ev):
    cfg = _cfg()
    base = [ev("terminal", {}, status="error", error_type="tool_error", error_message="AssertionError")]
    signals = detectors.evaluate(base, cfg)
    signals["session_trajectory_recurrence"] = 1
    assert policy.score_delta(signals, cfg, event=base[-1]) == 0

    active = [
        base[0],
        ev("patch", {}, '{"success": true}', is_mutation=True, mutation_landed=True),
        ev("terminal", {}, status="error", error_type="tool_error", error_message="AssertionError"),
    ]
    signals = detectors.evaluate(active, cfg)
    signals["session_trajectory_recurrence"] = 1
    assert policy.score_delta(signals, cfg, event=active[-1]) == 3
