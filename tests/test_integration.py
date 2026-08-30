"""Integration tests: simulate the Hermes agent loop (handoff §21).

Scenarios A–E drive the plugin's registered hooks exactly the way Hermes'
tool executor does (pre -> post -> transform), then assert on decisions.
"""

from hermes_plugins.progress_guard.config import ProgressGuardConfig

RECOVERY_MARKER = "PROGRESS GUARD: CURRENT STRATEGY STALLED"
HARD_STOP_MARKER = "PROGRESS GUARD: STRATEGY EXHAUSTED"


def ok(tool, args, result="ok"):
    return (tool, args, result, "ok", None, None)


def _injections(records):
    return [t for phase, t in records if phase == "ran" and t and RECOVERY_MARKER in t]


def _blocks(records):
    return [m for phase, m in records if phase == "blocked"]


def test_scenario_a_exact_loop_detected_and_blocked(drive):
    script = [ok("read_file", {"path": "/a"}, "same-content") for _ in range(6)]
    ctx, guard, records = drive(script)

    injects = _injections(records)
    blocks = _blocks(records)
    assert len(injects) == 1  # one guided recovery before the hard stop
    assert len(blocks) == 1
    assert HARD_STOP_MARKER in blocks[0]
    assert guard.metrics.snapshot().get("recoveries_triggered", 0) == 1
    assert guard.metrics.snapshot().get("exact_repeats", 0) >= 1


def test_scenario_b_alternating_loop_cycle_detected(drive):
    script = []
    for i in range(4):
        script.append(ok("A", {"i": i}, f"r{i}a"))
        script.append(ok("B", {"i": i}, f"r{i}b"))
    ctx, guard, records = drive(script)

    injects = _injections(records)
    blocks = _blocks(records)
    assert len(injects) == 1
    assert len(blocks) == 1
    assert HARD_STOP_MARKER in blocks[0]
    assert guard.metrics.snapshot().get("cycles_detected", 0) >= 1


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
        ok("write_file", {"path": "/x", "content": "C"}, "written"),  # mutation
        ok("terminal", {"command": "pytest"}, "passed"),               # mutation
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
            ok("write_file", {"path": "/x", "content": "C"}, "written"),
            ok("write_file", {"path": "/y", "content": "D"}, "written"),
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
