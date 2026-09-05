"""Integration tests: simulate the Hermes agent loop (handoff §21).

Scenarios A–E drive the plugin's registered hooks exactly the way Hermes'
tool executor does (pre -> post -> transform), then assert on decisions.
"""

from hermes_plugins.progress_guard.config import ProgressGuardConfig

RECOVERY_MARKER = "PROGRESS GUARD: CURRENT STRATEGY STALLED"
HARD_STOP_MARKER = "PROGRESS GUARD: STRATEGY EXHAUSTED"
THINKING_MARKER = "PROGRESS GUARD: THINKING LOOP DETECTED"


def ok(tool, args, result="ok"):
    return (tool, args, result, "ok", None, None)


def err(tool, args, err_type, err_msg):
    return (tool, args, None, "error", err_type, err_msg)


def _reason(drive_ctx, block, session="s1", turn="t1", iteration="1", times=6):
    """Stream a reasoning block verbatim `times` times (one line per delta)."""
    for _ in range(times):
        drive_ctx.hooks["on_stream_delta"](
            delta=block + "\n", kind="reasoning",
            session_id=session, turn_id=turn, iteration=iteration,
        )


def _injections(records):
    return [t for phase, t in records if phase == "ran" and t and RECOVERY_MARKER in t]


def _blocks(records):
    return [m for phase, m in records if phase == "blocked"]


def test_scenario_a_exact_loop_detected_and_blocked(drive):
    # A pure identical loop: recovery is granted twice (budget), a third
    # persistence of the same loop escalates to a hard stop. 13 identical
    # reads reach the third recurrence on the final event.
    script = [ok("read_file", {"path": "/a"}, "same-content") for _ in range(13)]
    ctx, guard, records = drive(script)

    injects = _injections(records)
    blocks = _blocks(records)
    assert len(injects) == 2  # two guided recoveries before the hard stop
    assert len(blocks) == 1
    assert HARD_STOP_MARKER in blocks[0]
    assert guard.metrics.snapshot().get("recoveries_triggered", 0) == 2
    assert guard.metrics.snapshot().get("hard_stops", 0) == 1
    assert guard.metrics.snapshot().get("exact_repeats", 0) >= 1


def test_scenario_b_alternating_loop_cycle_detected(drive):
    # Alternating loop A/B: recoveries at the 6th and 12th events, the third
    # recurrence (18th) escalates to a hard stop; a trailing call gets blocked.
    script = []
    for i in range(9):
        script.append(ok("A", {"i": i}, f"r{i}a"))
        script.append(ok("B", {"i": i}, f"r{i}b"))
    script.append(ok("A", {"i": 99}, "stray"))
    ctx, guard, records = drive(script)

    injects = _injections(records)
    blocks = _blocks(records)
    assert len(injects) == 2
    assert len(blocks) == 1
    assert HARD_STOP_MARKER in blocks[0]
    assert guard.metrics.snapshot().get("cycles_detected", 0) >= 1
    assert guard.metrics.snapshot().get("hard_stops", 0) == 1


def test_scenario_c_legitimate_polling_continues(drive):
    script = [
        ok("job_poll", {"job_id": "j1"}, "10%"),
        ok("job_poll", {"job_id": "j1"}, "30%"),
        ok("job_poll", {"job_id": "j1"}, "70%"),
        ok("job_poll", {"job_id": "j1"}, "completed"),
    ]
    ctx, guard, records = drive(script)

    assert _injections(records) == []
    assert _blocks(records) == []
    m = guard.metrics.snapshot()
    assert m.get("recoveries_triggered", 0) == 0
    assert m.get("exact_repeats", 0) == 0  # polls exempt from exact_repeat
    assert all(phase == "ran" for phase, _ in records)


def test_scenario_d_recovery_successful_no_hard_stop(drive):
    script = [
        ok("A", {"i": 0}, "r0"), ok("B", {"i": 0}, "r0"),
        ok("A", {"i": 1}, "r1"), ok("B", {"i": 1}, "r1"),
        ok("A", {"i": 2}, "r2"), ok("B", {"i": 2}, "r2"),  # -> RECOVER
        ok("write_file", {"path": "/x", "content": "C"}, '{"bytes_written": 5}'),  # landed mutation
        ok("terminal", {"command": "pytest"}, '{"exit_code": 0}'),               # mutation (no landed evidence)
    ]
    ctx, guard, records = drive(script)

    injects = _injections(records)
    assert len(injects) == 1
    assert _blocks(records) == []
    assert records[-1][0] == "ran"
    m = guard.metrics.snapshot()
    assert m.get("recoveries_triggered", 0) == 1
    assert m.get("hard_stops", 0) == 0


def test_scenario_e_recovery_budget_exhausted_hard_stop(drive):
    def loop():
        return [
            ok("A", {"i": 0}, "r0"), ok("B", {"i": 0}, "r0"),
            ok("A", {"i": 1}, "r1"), ok("B", {"i": 1}, "r1"),
            ok("A", {"i": 2}, "r2"), ok("B", {"i": 2}, "r2"),
        ]

    def reset():
        return [
            ok("write_file", {"path": "/x", "content": "C"}, '{"bytes_written": 5}'),
            ok("write_file", {"path": "/y", "content": "D"}, '{"bytes_written": 4}'),
        ]

    script = loop() + reset() + loop() + reset() + loop()
    script.append(ok("A", {"i": 99}, "stray"))  # next call hits the guard

    ctx, guard, records = drive(script)

    injects = _injections(records)
    blocks = _blocks(records)
    assert len(injects) == 2  # exactly max_attempts granted, 3rd escalated
    assert len(blocks) == 1
    assert HARD_STOP_MARKER in blocks[0]
    m = guard.metrics.snapshot()
    assert m.get("recoveries_triggered", 0) == 2
    assert m.get("hard_stops", 0) == 1


def test_state_isolated_by_session_and_turn(drive):
    script = [ok("read_file", {"path": "/a"}, "same") for _ in range(3)]
    _, guard, records = drive(script, session="s1", turn="t1")
    assert guard.registry.size() == 1
    s1 = guard.registry.get("s1", "t1")
    assert s1.stall_score >= 0

    # same session, different turn -> fresh state, untouched by t1
    t2_fresh = guard.registry.get("s1", "t2")
    assert t2_fresh.stall_score == 0 and len(t2_fresh.events) == 0
    s1_before = s1.stall_score
    drive(script, session="s1", turn="t2", guard=guard)
    assert guard.registry.get("s1", "t1").stall_score == s1_before
    assert guard.registry.get("s1", "t2").stall_score >= 0
    assert guard.registry.size() == 2

    # different session -> fresh state
    drive(script, session="s2", turn="t1", guard=guard)
    assert guard.registry.get("s2", "t1").stall_score >= 0


def test_session_end_cleans_state(drive):
    ctx, guard, _ = drive(
        [ok("read_file", {"path": "/a"}, "same") for _ in range(2)],
        session="s9", turn="t9",
    )
    assert guard.registry.size() == 1
    ctx.hooks["on_session_end"](task_id="s9", turn_id="t9")
    assert guard.registry.size() == 0


def test_disabled_guard_does_nothing(drive):
    script = [ok("read_file", {"path": "/a"}, "same") for _ in range(8)]
    ctx, guard, records = drive(script, settings={"enabled": False})
    assert _blocks(records) == []
    assert _injections(records) == []
    assert guard.metrics.snapshot() == {}


def test_thinking_loop_detected_and_injects_on_next_tool(make_guard):
    ctx, guard = make_guard()
    _reason(ctx, "The glob output showed a slightly different name. Let me iterate over the parent directory instead.")
    m = guard.metrics.snapshot()
    assert m.get("thinking_loops", 0) >= 1
    assert guard.registry.get("s1", "t1").stall_score >= 4

    # the model finally emits a tool call -> its result carries the guidance
    call_id = "call-1"
    ctx.hooks["pre_tool_call"](tool_name="search_files", session_id="s1", turn_id="t1")
    ctx.hooks["post_tool_call"](
        tool_name="search_files", args={"q": "x"}, result="found",
        session_id="s1", turn_id="t1", tool_call_id=call_id, status="ok",
    )
    transformed = ctx.hooks["transform_tool_result"](
        tool_name="search_files", result="found",
        session_id="s1", turn_id="t1", tool_call_id=call_id,
    )
    assert THINKING_MARKER in transformed


def test_thinking_loop_escapes_to_hard_stop(make_guard):
    ctx, guard = make_guard()
    state = guard.registry.get("s1", "t1")
    for iteration in range(3):
        _reason(ctx, "same recurring thought without progress", iteration=str(iteration + 1))
    # 3 loops > max_attempts(2) -> recovery budget exhausted
    assert state.recovery_count == 3
    assert state.hard_stop is True

    # next tool call is hard-blocked
    pre = ctx.hooks["pre_tool_call"](tool_name="search_files", session_id="s1", turn_id="t1")
    assert pre is not None and pre["action"] == "block"
    assert HARD_STOP_MARKER in pre["message"]


def test_thinking_loop_ignores_diverse_reasoning(make_guard):
    ctx, guard = make_guard()
    for line in ["check the path", "check the glob output", "em-dash encoding", "iterate over parent dir"]:
        ctx.hooks["on_stream_delta"](
            delta=line + "\n", kind="reasoning",
            session_id="s1", turn_id="t1", iteration="1",
        )
    assert guard.metrics.snapshot().get("thinking_loops", 0) == 0
    assert guard.registry.get("s1", "t1").stall_score == 0


def test_thinking_loop_resets_per_iteration(make_guard):
    ctx, guard = make_guard()
    # one repetition per iteration is normal (no run >= threshold within an iteration)
    for i in range(4):
        for _ in range(2):
            ctx.hooks["on_stream_delta"](
                delta="repeated-ish thought\n", kind="reasoning",
                session_id="s1", turn_id="t1", iteration=str(i + 1),
            )
    assert guard.metrics.snapshot().get("thinking_loops", 0) == 0
    assert guard.registry.get("s1", "t1").stall_score == 0


def test_reasoning_abab_cycle_detected(make_guard):
    ctx, guard = make_guard()
    for line in ["rethink plan", "check the path", "rethink plan", "check the path"]:
        ctx.hooks["on_stream_delta"](
            delta=line + "\n", kind="reasoning",
            session_id="s1", turn_id="t1", iteration="1",
        )
    m = guard.metrics.snapshot()
    assert m.get("reasoning_cycles", 0) >= 1
    assert guard.registry.get("s1", "t1").reasoning_flagged is True


def test_reasoning_abcabc_cycle_detected(make_guard):
    ctx, guard = make_guard()
    block = ["think a", "think b", "think c"]
    for _ in range(2):
        for line in block:
            ctx.hooks["on_stream_delta"](
                delta=line + "\n", kind="reasoning",
                session_id="s1", turn_id="t1", iteration="1",
            )
    m = guard.metrics.snapshot()
    assert m.get("reasoning_cycles", 0) >= 1


def test_reasoning_cycle_marks_recovery_and_injects(make_guard):
    ctx, guard = make_guard()
    for line in ["A", "B", "A", "B"]:
        ctx.hooks["on_stream_delta"](
            delta=line + "\n", kind="reasoning",
            session_id="s1", turn_id="t1", iteration="1",
        )
    state = guard.registry.get("s1", "t1")
    assert state.pending_thinking_recovery is True
    transformed = ctx.hooks["transform_tool_result"](
        tool_name="search_files", result="found",
        session_id="s1", turn_id="t1", tool_call_id="c1",
    )
    assert THINKING_MARKER in transformed


# --- Phase 1.6 trajectory/convergence scenarios (handoff §24) --------------

def test_action_family_cycle_detected(drive):
    # read_file/grep/read_file/search_files/read_file/grep is a READ/SEARCH
    # intent cycle even though no concrete tool repeats (handoff §8, §24)
    script = [
        ok("read_file", {"path": "/a"}, "r1"),
        ok("grep", {"pattern": "x"}, "r2"),
        ok("read_file", {"path": "/b"}, "r3"),
        ok("search_files", {"q": "x"}, "r4"),
        ok("read_file", {"path": "/c"}, "r5"),
        ok("grep", {"pattern": "y"}, "r6"),
    ]
    ctx, guard, records = drive(script)

    assert len(_injections(records)) >= 1
    assert len(_blocks(records)) == 0
    m = guard.metrics.snapshot()
    assert m.get("action_family_cycles", 0) >= 1
    assert m.get("cycles_detected", 0) == 0  # exact tool names never repeated


def test_mutation_success_does_not_imply_progress(drive):
    # patch "succeeds" (landed) yet the same test failure repeats -> stall.
    # The landed mutation is material on its own, but it grants no immunity:
    # 3+ identical failures still accumulate into a RECOVER (handoff §4, §24)
    script = [
        ok("patch", {"file": "app.py"}, '{"success": true}'),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 9"),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 9"),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 9"),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 9"),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 9"),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 9"),
    ]
    ctx, guard, records = drive(script)

    assert len(_injections(records)) >= 1
    m = guard.metrics.snapshot()
    assert m.get("repeated_failures", 0) >= 1
    assert m.get("material_progress_events", 0) >= 1  # the landed patch counted


def test_recovery_then_strategy_change_not_re_blocked(drive):
    # Real CLI session 20260902_234056_c5cebd regression: identical terminal
    # misuse errors triggered recovery guidance, the model THEN changed
    # strategy (background=true, pytest green) and did real work. The recovery
    # checkpoint must reset the detector window so post-recovery work is never
    # hard-blocked by pre-recovery evidence (second over-block mechanism).
    script = (
        [ok("terminal", {"command": "git status"}, "clean")]
        + [err("terminal", {"command": "notify x"},
               "tool_error", "notify must be true/false") for _ in range(5)]
        + [ok("terminal", {"command": "sleep 3", "background": True},
              '{"pid": 47717, "status": "running"}'),
           ok("terminal", {"command": "pytest"}, '{"exit_code": 0, "passed": 109}'),
           ok("terminal", {"command": "ps aux"}, "no matching process")]
    )
    ctx, guard, records = drive(script)

    assert len(_injections(records)) == 1  # one guided recovery, delivered
    assert _blocks(records) == []          # strategy change keeps working
    m = guard.metrics.snapshot()
    assert m.get("recoveries_triggered", 0) == 1
    assert m.get("repeated_failures", 0) >= 1
    assert guard.registry.get("s1", "t1").stall_score < 5


def test_iterative_progress_never_false_stalls(drive):
    # patch -> tests improve -> patch -> tests pass: real progress, no stall
    # (handoff §24 "landed-mutation-with-progress")
    script = [
        ok("patch", {"file": "a.py"}, '{"success": true}'),
        err("terminal", {"command": "pytest"}, "tool_error", "2 tests failed"),
        ok("patch", {"file": "a.py"}, '{"success": true}'),
        err("terminal", {"command": "pytest"}, "tool_error", "1 test failed"),
        ok("patch", {"file": "a.py"}, '{"success": true}'),
        ok("terminal", {"command": "pytest"}, '{"exit_code": 0, "passed": 42}'),
    ]
    ctx, guard, records = drive(script)

    assert _injections(records) == []
    assert _blocks(records) == []
    m = guard.metrics.snapshot()
    assert m.get("recoveries_triggered", 0) == 0
    assert m.get("repeated_failures", 0) == 0  # failures improved each time


def test_novelty_without_progress_tracks_steps_but_never_stalls(drive):
    # 4 distinct searches with distinct results: steps climb, decision stays
    # CONTINUE — novelty is not stagnation (handoff §10, §24)
    script = [
        ok("search_files", {"query": "alpha"}, "res alpha"),
        ok("search_files", {"query": "beta"}, "res beta"),
        ok("search_files", {"query": "gamma"}, "res gamma"),
        ok("search_files", {"query": "delta"}, "res delta"),
    ]
    ctx, guard, records = drive(script)

    assert _injections(records) == []
    assert _blocks(records) == []
    state = guard.registry.get("s1", "t1")
    assert state.steps_since_material_progress == 4
    assert state.stall_score == 0


def test_legitimate_polling_counts_material_progress(drive):
    script = [
        ok("job_poll", {"job_id": "j1"}, "10%"),
        ok("job_poll", {"job_id": "j1"}, "40%"),
        ok("job_poll", {"job_id": "j1"}, "80%"),
        ok("job_poll", {"job_id": "j1"}, "completed"),
    ]
    ctx, guard, records = drive(script)

    assert _injections(records) == []
    assert _blocks(records) == []
    m = guard.metrics.snapshot()
    assert m.get("material_progress_events", 0) >= 2  # advances + completion
    state = guard.registry.get("s1", "t1")
    assert state.last_poll_done is True
    assert state.steps_since_material_progress == 0  # reset at completion


def test_hard_stop_ignored_counts_post_block_calls(drive):
    # Model keeps issuing tools after each recovery message and even after the
    # hard-stop block -> post-block attempts are counted as evidence.
    script = [ok("read_file", {"path": "/a"}, "same") for _ in range(15)]
    ctx, guard, records = drive(script)

    blocks = _blocks(records)
    assert len(blocks) == 3  # the 13th, 14th and 15th calls hit the hard stop
    assert all(HARD_STOP_MARKER in b for b in blocks)
    m = guard.metrics.snapshot()
    assert m.get("hard_stops", 0) == 1
    assert m.get("blocked_calls_after_hard_stop", 0) == 2


def test_session_carryover_folds_and_cleans(drive):
    # turn 1 leaves a family/failure trail; internal continuation (new turn,
    # same session) keeps the rolling trajectory; on_session_start/finalize
    # are the real session boundaries (handoff §15)
    ctx, guard, records = drive(
        [err("read_file", {"path": "/a"}, "tool_error", "Permission denied"),
         ok("search_files", {"query": "q"}, "found")],
        session="sx", turn="t1",
    )
    ctx.hooks["on_session_end"](session_id="sx", turn_id="t1")
    traj = guard.registry.get_session("sx")
    assert traj.carryovers == 1
    assert "READ" in list(traj.recent_families)
    assert traj.recent_failure_signatures  # the denied read survived

    # a genuine new session resets the trajectory
    ctx.hooks["on_session_start"](session_id="sx")
    assert guard.registry.get_session("sx").carryovers == 0

    # real teardown drops all session state
    drive([ok("read_file", {"path": "/a"}, "same")], session="sx", turn="t2", guard=guard)
    ctx.hooks["on_session_finalize"](session_id="sx")
    assert guard.registry.size() == 0
    assert guard.registry.session_count() == 0


def test_session_finalize_logs_metrics_summary_when_debug(drive, caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger="progress-guard")
    ctx, guard, records = drive(
        [ok("patch", {"file": "a.py"}, '{"success": true}'),
         err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 9"),
         err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 9"),
         err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 9")],
        settings={"debug": True},
        session="sy", turn="t1",
    )
    ctx.hooks["on_session_finalize"](session_id="sy", platform="cli")
    lines = [r.getMessage() for r in caplog.records
             if "SESSION SUMMARY" in r.getMessage()]
    assert lines, "no SESSION SUMMARY logged"
    assert lines[-1].startswith("[progress-guard] SESSION SUMMARY session=sy platform=cli ")
    assert "material_progress_events=1" in lines[-1]
    assert "repeated_failures=1" in lines[-1]


def test_duplicate_write_burst_does_not_poison_legit_work(drive):
    # Regression from real-session evaluation (T1): a 3x identical write_file
    # burst (client re-emission) must not keep firing exact_repeat on later
    # DISTINCT landed writes; material decay keeps the score low and the
    # legit workflow runs to completion without injection/block.
    script = [
        ok("write_file", {"path": "/s/__main__.py", "content": "cli = 1"}, '{"bytes_written": 5}'),
        ok("write_file", {"path": "/s/__main__.py", "content": "cli = 1"}, '{"bytes_written": 5}'),
        ok("write_file", {"path": "/s/__main__.py", "content": "cli = 1"}, '{"bytes_written": 5}'),
        ok("write_file", {"path": "/s/lib.py", "content": "def top(): ..."}, '{"bytes_written": 7}'),
        ok("write_file", {"path": "/s/tests.py", "content": "def test_top(): ..."}, '{"bytes_written": 9}'),
        ok("execute_code", {"code": "run_tests()"}, '{"exit_code": 0, "passed": 3}'),
    ]
    ctx, guard, records = drive(script)

    assert _injections(records) == []
    assert _blocks(records) == []
    state = guard.registry.get("s1", "t1")
    assert state.stall_score == 0  # distinct landed writes decayed the burst
    assert guard.metrics.snapshot().get("material_progress_events", 0) >= 1


# --- Phase 2A failure recurrence scenarios ---------------------------------

def test_same_failure_after_landed_mutation_recovers(drive):
    script = [
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 9"),
        ok("patch", {"file": "a.py"}, '{"success": true}'),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 12"),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError at line 15"),
    ]
    ctx, guard, records = drive(script)

    assert len(_injections(records)) == 1
    m = guard.metrics.snapshot()
    assert m.get("same_failure_after_mutation", 0) >= 1


def test_failure_improvement_after_landed_mutation_does_not_recover(drive):
    script = [
        err("terminal", {"command": "pytest"}, "tool_error", "2 tests failed"),
        ok("patch", {"file": "a.py"}, '{"success": true}'),
        err("terminal", {"command": "pytest"}, "tool_error", "1 test failed"),
    ]
    ctx, guard, records = drive(script)

    assert _injections(records) == []
    assert guard.metrics.snapshot().get("failure_improvements", 0) >= 1


def test_post_recovery_recurrence_does_not_spam_same_guidance(drive):
    script = [
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError"),
        ok("patch", {"file": "a.py"}, '{"success": true}'),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError"),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError"),
        ok("patch", {"file": "a.py"}, '{"success": true}'),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError"),
        err("terminal", {"command": "pytest"}, "tool_error", "AssertionError"),
    ]
    ctx, guard, records = drive(script)

    assert len(_injections(records)) == 1
    m = guard.metrics.snapshot()
    assert m.get("post_recovery_recurrences", 0) >= 1
    assert m.get("suppressed_duplicate_recoveries", 0) >= 1
