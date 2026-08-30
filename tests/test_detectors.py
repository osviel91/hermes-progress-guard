"""Unit tests: detectors (handoff §10, §20)."""

from hermes_plugins.progress_guard import detectors
from hermes_plugins.progress_guard.config import ProgressGuardConfig


def _cfg(**overrides):
    return ProgressGuardConfig.from_mapping(overrides)


_SECTIONS = {
    detectors.exact_repeat: "exact_repeat",
    detectors.identical_result: "identical_result",
    detectors.cycle: "cycle",
    detectors.repeated_failure: "repeated_failure",
}


def _run(detector, events, cfg=None):
    cfg = cfg or _cfg()
    section = _SECTIONS.get(detector)
    if section:
        cfg = getattr(cfg, section)
    return detector(events, cfg)


def test_exact_repeat_detects_triple_aaa(ev):
    events = [ev("tool", {"x": 1}, "r") for _ in range(3)]  # same args fingerprint
    assert _run(detectors.exact_repeat, events) == 3


def test_exact_repeat_no_false_positive_abc(ev):
    events = [ev("a", {"q": 1}, "r1"), ev("b", {"q": 1}, "r2"), ev("c", {"q": 1}, "r3")]
    assert _run(detectors.exact_repeat, events) == 1


def test_exact_repeat_max_run_across_break(ev):
    events = [
        ev("a", {"q": 1}, "r"), ev("a", {"q": 1}, "r"), ev("b", {"q": 1}, "r"),
        ev("a", {"q": 1}, "r"),
    ]
    assert _run(detectors.exact_repeat, events) == 2


def test_identical_result_detects_stagnant_search(ev):
    events = [
        ev("search", {"query": "A"}, "same-result"),
        ev("search", {"query": "B"}, "same-result"),
        ev("search", {"query": "C"}, "same-result"),
    ]
    assert _run(detectors.identical_result, events) == 3


def test_identical_result_polling_exempt(ev):
    events = [
        ev("job_poll", {"id": "j1"}, "10%", is_poll=True),
        ev("job_poll", {"id": "j2"}, "10%", is_poll=True),
        ev("job_poll", {"id": "j3"}, "10%", is_poll=True),
    ]
    assert _run(detectors.identical_result, events) == 0


def test_identical_result_same_action_not_counted(ev):
    events = [
        ev("read_file", {"path": "/a"}, "content"),
        ev("read_file", {"path": "/a"}, "content"),
        ev("read_file", {"path": "/a"}, "content"),
    ]
    assert _run(detectors.identical_result, events) == 1  # one distinct action


def test_cycle_detects_abab(ev):
    events = [
        ev("A", {}, "r1"), ev("B", {}, "r2"),
        ev("A", {}, "r3"), ev("B", {}, "r4"),
    ]
    assert _run(detectors.cycle, events) is True


def test_cycle_detects_abcabc(ev):
    events = [
        ev("A", {}, "r1"), ev("B", {}, "r2"), ev("C", {}, "r3"),
        ev("A", {}, "r4"), ev("B", {}, "r5"), ev("C", {}, "r6"),
    ]
    assert _run(detectors.cycle, events) is True


def test_cycle_short_sequence_no_detect(ev):
    events = [ev("A", {}, "r1"), ev("B", {}, "r2"), ev("C", {}, "r3")]
    assert _run(detectors.cycle, events) is False


def test_cycle_same_tool_repeat_is_not_cycle(ev):
    # 4 identical poll calls must not register as a cycle (>=2 tools required)
    events = [ev("poll", {}, "r", is_poll=True) for _ in range(4)]
    assert _run(detectors.cycle, events) is False


def test_cycle_respects_window(ev):
    cfg = _cfg(cycle={"enabled": True, "window": 4, "max_cycle_length": 2, "repetitions": 2})
    events = [
        ev("A", {}, "r1"), ev("B", {}, "r2"), ev("A", {}, "r3"), ev("B", {}, "r4"),
        ev("X", {}, "r5"), ev("Y", {}, "r6"), ev("X", {}, "r7"), ev("Y", {}, "r8"),
    ]
    assert _run(detectors.cycle, events, cfg) is True  # last 4: X Y X Y


def test_repeated_failure_same_error_class(ev):
    events = [
        ev("patch", {"f": "a"}, status="error", error_type="PatchError", error_message="context mismatch line 3"),
        ev("patch", {"f": "b"}, status="error", error_type="PatchError", error_message="context mismatch line 8"),
        ev("patch", {"f": "c"}, status="error", error_type="PatchError", error_message="context mismatch line 12"),
    ]
    assert _run(detectors.repeated_failure, events) == 3


def test_repeated_failure_distinguishes_error_classes(ev):
    events = [
        ev("patch", {"f": "a"}, status="error", error_type="PatchError", error_message="context mismatch"),
        ev("patch", {"f": "b"}, status="error", error_type="PatchError", error_message="context mismatch"),
        ev("patch", {"f": "c"}, status="error", error_type="TimeoutError", error_message="timed out"),
    ]
    assert _run(detectors.repeated_failure, events) == 2


def test_repeated_failure_ignores_successes(ev):
    events = [
        ev("patch", {"f": "a"}, "ok"),
        ev("patch", {"f": "a"}, "ok"),
        ev("patch", {"f": "a"}, "ok"),
    ]
    assert _run(detectors.repeated_failure, events) == 0
