"""Unit tests: detectors (handoff §10, §20)."""

from hermes_plugins.progress_guard import detectors
from hermes_plugins.progress_guard.config import ProgressGuardConfig


def _cfg(**overrides):
    return ProgressGuardConfig.from_mapping(overrides)


_SECTIONS = {
    detectors.exact_repeat: "exact_repeat",
    detectors.identical_result: "identical_result",
    detectors.cycle: "cycle",
    detectors.family_cycle: "family_cycle",
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


def test_exact_repeat_tail_run_breaks_at_interruption(ev):
    # An old duplicate burst that stopped being extended must not keep firing
    # on later events (real-session over-block regression). Only the run the
    # current event is part of counts.
    events = [
        ev("a", {"q": 1}, "r"), ev("a", {"q": 1}, "r"), ev("b", {"q": 1}, "r"),
        ev("a", {"q": 1}, "r"),
    ]
    assert _run(detectors.exact_repeat, events) == 1
    # ...while an uninterrupted run still escalates to the full length
    assert _run(detectors.exact_repeat, [ev("a", {"q": 1}, "r")] * 4) == 4


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


def test_cycle_ignores_mutation_iteration(ev):
    # write_file -> terminal -> write_file -> terminal is iterative dev
    # (write, run, tweak, run) = progress, NOT a loop (handoff §16)
    events = [
        ev("write_file", {"f": "a"}, "ok", is_mutation=True),
        ev("terminal", {}, "r1", is_mutation=True),
        ev("write_file", {"f": "b"}, "ok", is_mutation=True),
        ev("terminal", {}, "r2", is_mutation=True),
    ]
    assert _run(detectors.cycle, events) is False


def test_cycle_still_detects_read_search_alternation(ev):
    events = [
        ev("search", {"q": "x"}, "r1"), ev("read_file", {}, "r2"),
        ev("search", {"q": "x"}, "r3"), ev("read_file", {}, "r4"),
    ]
    assert _run(detectors.cycle, events) is True


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


def test_repeated_failure_none_message_distinguishes_by_output(ev):
    # Hermes reports terminal failures with error_message="None"; different
    # outputs must not merge into one failure class.
    events = [
        ev("terminal", {"c": "yt-dlp --version"}, status="error", error_type="tool_error", error_message="None", result="yt-dlp: command not found"),
        ev("terminal", {"c": "run 1"}, status="error", error_type="tool_error", error_message="None", result="[Command timed out after 180s]"),
        ev("terminal", {"c": "run 2"}, status="error", error_type="tool_error", error_message="None", result="[Command timed out after 180s]"),
        ev("terminal", {"c": "mid3cp"}, status="error", error_type="tool_error", error_message="None", result="mid3cp: warning: uv bin not in PATH"),
        ev("terminal", {"c": "run 3"}, status="error", error_type="tool_error", error_message="None", result="Traceback NameError: ALBUM at line 4"),
        ev("terminal", {"c": "run 4"}, status="error", error_type="tool_error", error_message="None", result="cleaned. album now:"),
    ]
    assert _run(detectors.repeated_failure, events) == 2  # only the 2 identical timeouts merge


def test_repeated_failure_none_message_same_output_groups(ev):
    events = [
        ev("terminal", {"c": "x"}, status="error", error_type="tool_error", error_message="None", result="No such file or directory"),
        ev("terminal", {"c": "y"}, status="error", error_type="tool_error", error_message="None", result="No such file or directory"),
        ev("terminal", {"c": "z"}, status="error", error_type="tool_error", error_message="None", result="No such file or directory"),
    ]
    assert _run(detectors.repeated_failure, events) == 3


def test_repeated_failure_generic_message_distinguishes_by_output(ev):
    # Hermes gives ALL terminal failures error_type='tool_error' +
    # 'Script exited with code 1'; the distinguishing content is in the result.
    # 4 genuinely different inspection scripts must NOT merge (regression for
    # the 20260830_202815_106de3 false positive).
    events = [
        ev("terminal", {"cmd": "py script1.py"}, "FILE | ARTIST | TITLE | ALBUM | TRACK | YEAR | GENRE",
           status="error", error_type="tool_error", error_message="Script exited with code 1"),
        ev("terminal", {"cmd": "py script2.py"}, "FILE | ARTIST | TITLE | TRACK | YEAR | nonascii#",
           status="error", error_type="tool_error", error_message="Script exited with code 1"),
        ev("terminal", {"cmd": "py script3.py"}, "Traceback: KeyError at line 12 in mapper",
           status="error", error_type="tool_error", error_message="Script exited with code 1"),
        ev("terminal", {"cmd": "py script4.py"}, "FILE | ARTIST | TITLE | TRACK | YEAR | DUR/BIT",
           status="error", error_type="tool_error", error_message="Script exited with code 1"),
    ]
    assert _run(detectors.repeated_failure, events) == 1


def test_repeated_failure_generic_message_same_output_groups(ev):
    # identical failure output (e.g. same cd typo twice) still groups
    events = [
        ev("terminal", {"cmd": "cd /Volumes/personal_folder/Music/Dake Academia"},
           "no such file or directory", status="error",
           error_type="tool_error", error_message="Script exited with code 1"),
        ev("terminal", {"cmd": "cd /Volumes/personal_folder/Music/Dake Academia"},
           "no such file or directory", status="error",
           error_type="tool_error", error_message="Script exited with code 1"),
        ev("terminal", {"cmd": "cd /Volumes/personal_folder/Music/Dake Academia"},
           "no such file or directory", status="error",
           error_type="tool_error", error_message="Script exited with code 1"),
    ]
    assert _run(detectors.repeated_failure, events) == 3


def test_repeated_failure_line_number_noise_ignored(ev):
    # patch(A)/patch(B) 'context mismatch at line N' still collapse (handoff §10.4)
    events = [
        ev("patch", {"f": "a"}, "context mismatch at line 3",
           status="error", error_type="PatchError", error_message="context mismatch at line 3"),
        ev("patch", {"f": "b"}, "context mismatch at line 8",
           status="error", error_type="PatchError", error_message="context mismatch at line 8"),
        ev("patch", {"f": "c"}, "context mismatch at line 12",
           status="error", error_type="PatchError", error_message="context mismatch at line 12"),
    ]
    assert _run(detectors.repeated_failure, events) == 3


def test_same_failure_after_landed_mutation_counts_recurrence(ev):
    events = [
        ev("terminal", {"command": "pytest"}, status="error", error_type="tool_error", error_message="AssertionError at line 9"),
        ev("patch", {"file": "a.py"}, '{"success": true}', is_mutation=True, mutation_landed=True, material=True),
        ev("terminal", {"command": "pytest"}, status="error", error_type="tool_error", error_message="AssertionError at line 12"),
        ev("terminal", {"command": "pytest"}, status="error", error_type="tool_error", error_message="AssertionError at line 15"),
    ]
    assert detectors.same_failure_after_mutation(events[:3]) == 1
    assert detectors.same_failure_after_mutation(events) == 2


def test_same_failure_after_mutation_needs_landed_mutation(ev):
    events = [
        ev("terminal", {}, status="error", error_type="tool_error", error_message="AssertionError"),
        ev("patch", {}, "applied", is_mutation=True),
        ev("terminal", {}, status="error", error_type="tool_error", error_message="AssertionError"),
    ]
    assert detectors.same_failure_after_mutation(events) == 0


def test_same_failure_after_mutation_ignores_different_failure(ev):
    events = [
        ev("terminal", {}, status="error", error_type="tool_error", error_message="AssertionError"),
        ev("patch", {}, '{"success": true}', is_mutation=True, mutation_landed=True),
        ev("terminal", {}, status="error", error_type="tool_error", error_message="ImportError"),
    ]
    assert detectors.same_failure_after_mutation(events) == 0


def test_same_failure_after_mutation_ignores_error_class_only(ev):
    events = [
        ev("terminal", {}, status="error", error_type="tool_error", error_message="A"),
        ev("patch", {}, '{"success": true}', is_mutation=True, mutation_landed=True),
        ev("terminal", {}, status="error", error_type="tool_error", error_message="B"),
    ]
    events = [
        type(e)(**{**e.__dict__, "failure_sig": None, "failure_group": None})
        for e in events
    ]
    assert detectors.same_failure_after_mutation(events) == 0


def test_failure_improvement_suppresses_same_failure_after_mutation(ev):
    events = [
        ev("terminal", {}, status="error", error_type="tool_error", error_message="2 tests failed"),
        ev("patch", {}, '{"success": true}', is_mutation=True, mutation_landed=True),
        ev("terminal", {}, status="error", error_type="tool_error", error_message="1 test failed"),
    ]
    assert detectors.failure_improvement(events) is True
    assert detectors.same_failure_after_mutation(events) == 0


def test_repeated_thinking_consecutive_identical(ev):
    segs = ["same thought", "same thought", "same thought"]
    assert detectors.repeated_thinking(segs, 3) == 3


def test_repeated_thinking_diverse_is_zero(ev):
    segs = ["check path", "check glob", "iterate parent"]
    assert detectors.repeated_thinking(segs, 3) == 1


def test_repeated_thinking_below_threshold(ev):
    segs = ["thought", "thought", "different"]
    assert detectors.repeated_thinking(segs, 3) == 2


def test_repeated_thinking_empty(ev):
    assert detectors.repeated_thinking([], 3) == 0


def test_repeated_thinking_blank_lines_skipped(ev):
    # blank lines are whitespace; identical non-blank segments still count
    segs = ["a", "", "a", "a"]
    assert detectors.repeated_thinking(segs, 3) == 3


def test_family_cycle_detects_cross_tool_read_search(ev):
    # read_file -> grep -> read_file -> search_files is READ/SEARCH at the
    # intent level even though the concrete tools never repeat (handoff §8)
    events = [
        ev("read_file", {"path": "/a"}, "r1"),
        ev("grep", {"q": "x"}, "r2"),
        ev("read_file", {"path": "/b"}, "r3"),
        ev("search_files", {"q": "x"}, "r4"),
        ev("read_file", {"path": "/c"}, "r5"),
        ev("grep", {"q": "y"}, "r6"),
    ]
    assert _run(detectors.family_cycle, events) == 2  # READ SEARCH READ SEARCH


def test_family_cycle_not_fired_for_single_family(ev):
    events = [
        ev("read_file", {"path": "/a"}, "r1"),
        ev("read_file", {"path": "/b"}, "r2"),
        ev("read_file", {"path": "/c"}, "r3"),
        ev("read_file", {"path": "/d"}, "r4"),
    ]
    assert _run(detectors.family_cycle, events) == 0


def test_family_cycle_ignores_mutation_period(ev):
    # a mutating tool inside the period means iterative development, not a loop
    events = [
        ev("read_file", {"path": "/a"}, "r1"),
        ev("write_file", {"path": "/x"}, '{"bytes_written": 3}', is_mutation=True),
        ev("read_file", {"path": "/a"}, "r2"),
        ev("write_file", {"path": "/y"}, '{"bytes_written": 3}', is_mutation=True),
    ]
    assert _run(detectors.family_cycle, events) == 0


def test_tool_cycle_still_detected_alongside_family(ev):
    # the exact tool-name detector is kept, not replaced by the family one
    events = [
        ev("A", {}, "r1"), ev("B", {}, "r2"),
        ev("A", {}, "r3"), ev("B", {}, "r4"),
    ]
    assert _run(detectors.cycle, events) is True
    assert _run(detectors.family_cycle, events) in (0, 2)  # A/B both OTHER


def test_reasoning_cycle_detects_abab():
    segs = ["why is this failing", "check the path", "why is this failing", "check the path"]
    assert detectors.reasoning_cycle(segs, 2, 3) == 2


def test_reasoning_cycle_detects_abcabc():
    segs = [
        "step one", "step two", "step three",
        "step one", "step two", "step three",
    ]
    assert detectors.reasoning_cycle(segs, 2, 3) == 3


def test_reasoning_cycle_ignores_diverse():
    segs = ["a", "b", "c", "d", "e", "f"]
    assert detectors.reasoning_cycle(segs, 2, 3) == 0


def test_reasoning_cycle_ignores_single_distinct():
    # pure repeats are repeated_thinking's job, not a cycle
    segs = ["same", "same", "same", "same"]
    assert detectors.reasoning_cycle(segs, 2, 3) == 0


def test_reasoning_normalization_is_cheap():
    assert detectors.normalize_segment("  A\nB   ") == "a b"
    assert detectors.normalize_segment("Done!") == "done!"
