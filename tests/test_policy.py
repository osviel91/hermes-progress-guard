"""Unit tests: policy scoring + decisions (handoff §11, §15, §20)."""

from hermes_plugins.progress_guard import detectors, policy
from hermes_plugins.progress_guard.config import ProgressGuardConfig


def _cfg(**overrides):
    return ProgressGuardConfig.from_mapping(overrides)


def _score(events, cfg=None, event=None, prev=None):
    cfg = cfg or _cfg()
    signals = detectors.evaluate(events, cfg)
    return policy.score_delta(signals, cfg, event=event, prev_result_fingerprint=prev)


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


def test_successful_mutation_decays_score(ev):
    cfg = _cfg()
    event = ev("patch", {"f": "a"}, "applied", is_mutation=True)
    signals = detectors.evaluate([event], cfg)
    delta = policy.score_delta(signals, cfg, event=event, prev_result_fingerprint=None)
    assert delta == -3  # floor handled by caller clamp


def test_changed_result_decays(ev):
    cfg = _cfg()
    a = ev("search", {"q": "x"}, "r1")
    b = ev("search", {"q": "x"}, "r2")
    signals = detectors.evaluate([a, b], cfg)
    delta = policy.score_delta(signals, cfg, event=b, prev_result_fingerprint=a.result_fingerprint)
    assert delta == -2


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
        repeated_failure={"enabled": False},
    )
    events = [
        ev("A", {"x": 1}, "r"), ev("A", {"x": 1}, "r"), ev("A", {"x": 1}, "r"),
    ]
    assert _score(events, cfg) == 0
