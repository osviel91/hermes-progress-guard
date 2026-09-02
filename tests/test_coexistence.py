"""Coexistence with tool-output-compactor (handoff §20, §24).

Hermes runs every transform_tool_result callback with the SAME raw result and
keeps only the first string by registration (== plugin load) order
(model_tools.py first-valid-string-wins). This mirrors that contract with a
fake compactor so the design intent is locked in a test, not left to drift.
"""

from hermes_plugins.progress_guard import fingerprint, normalize

RECOVERY_MARKER = "PROGRESS GUARD: CURRENT STRATEGY STALLED"
HARD_STOP_MARKER = "PROGRESS GUARD: STRATEGY EXHAUSTED"
COMPACTED_MARKER = "[tool-output-compactor compacted tool result]"


def fake_compactor(tool_name="", args=None, result=None, session_id="", turn_id="",
                   tool_call_id="", **_):
    # deterministic stand-in for the real compactor's "I am compacting" branch
    if isinstance(result, str) and len(result) > 20:
        return COMPACTED_MARKER + " " + result[:20]
    return None


def first_string_wins(callbacks, **kwargs):
    """Replicates agent/model_tools.py transform_tool_result semantics."""
    for cb in callbacks:
        out = cb(**kwargs)
        if isinstance(out, str):
            return out
    return None


def _read(ctx, guard, i, session="s1", turn="t1"):
    """one read_file call: pre+post, NO transform (keeps recovery pending)."""
    cid = f"read-{i}"
    ctx.hooks["post_tool_call"](
        tool_name="read_file", args={"path": "/a"}, result="same",
        session_id=session, turn_id=turn, tool_call_id=cid, status="ok",
    )


def test_post_hook_sees_raw_result_before_compaction(make_guard):
    # PG must analyze the RAW result in post_tool_call; the compactor only runs
    # later at transform_tool_result (handoff §20).
    ctx, guard = make_guard()
    raw = "x" * 5000  # big enough that the real compactor would compact it
    ctx.hooks["post_tool_call"](
        tool_name="read_file", args={"path": "/a"}, result=raw,
        session_id="s1", turn_id="t1", tool_call_id="c0", status="ok",
    )
    state = guard.registry.get("s1", "t1")
    expected = fingerprint.result_fingerprint(normalize.normalize_result(raw))
    assert state.events[-1].result_fingerprint == expected

    # running a compaction afterwards must not retroactively change the event
    first_string_wins([fake_compactor], tool_name="read_file", result=raw,
                      session_id="s1", turn_id="t1", tool_call_id="c0")
    assert state.events[-1].result_fingerprint == expected


def test_guard_first_recovery_survives_compaction(make_guard):
    # guard registers before the compactor (alphabetical load order today):
    # its recovery message wins and the compaction for that result is dropped.
    ctx, guard = make_guard()
    for i in range(4):
        _read(ctx, guard, i)  # 4th identical read -> RECOVER, pending set
    state = guard.registry.get("s1", "t1")
    assert state.pending_recovery is not None

    raw = "x" * 5000
    final = first_string_wins(
        [guard.on_transform_tool_result, fake_compactor],
        tool_name="read_file", result=raw, session_id="s1", turn_id="t1",
        tool_call_id="read-3",
    )
    assert RECOVERY_MARKER in final
    assert COMPACTED_MARKER not in final  # the compaction was dropped


def test_compactor_first_loses_recovery_message(make_guard):
    # If the compactor were ordered first and compacts, its string wins and the
    # recovery message is silently lost — the documented reason PG also steers
    # via pre_tool_call and never relies on transform alone (analysis doc §3).
    ctx, guard = make_guard()
    for i in range(4):
        _read(ctx, guard, i)
    state = guard.registry.get("s1", "t1")
    assert state.pending_recovery is not None

    raw = "x" * 5000
    final = first_string_wins(
        [fake_compactor, guard.on_transform_tool_result],
        tool_name="read_file", result=raw, session_id="s1", turn_id="t1",
        tool_call_id="read-3",
    )
    assert final.startswith(COMPACTED_MARKER)
    assert RECOVERY_MARKER not in final  # known loss, never depended on


def test_hard_stop_independent_of_transform_ordering(make_guard):
    # hard-stop enforcement happens in pre_tool_call (before transform hooks),
    # so transform ordering can never mute a block.
    ctx, guard = make_guard()
    for i in range(5):
        _read(ctx, guard, i)  # 5th identical read escalates past block
    pre = ctx.hooks["pre_tool_call"](
        tool_name="write_file", session_id="s1", turn_id="t1",
    )
    assert pre is not None
    assert pre["action"] == "block"
    assert HARD_STOP_MARKER in pre["message"]
