# Phase 2A: Safe Failure Recurrence

Status: planned

## Goal

Close the known blind spot where the agent keeps landing mutations but returns
to the same underlying failure. Keep this phase goal-agnostic, deterministic,
and biased toward false negatives.

## Non-Goals

- No LLM evaluator.
- No embeddings.
- No task hints or goal awareness.
- No generic `analysis_loop` detector.
- No generic `tool_churn` detector.
- No standalone `no_material_progress_window` recovery trigger.
- No more aggressive blocking policy until replay tests justify it.

## Work Plan

1. Audit current failure signature normalization.
2. Add hierarchical canonical failure grouping.
3. Add `same_failure_after_mutation`.
4. Detect measurable failure improvement where possible.
5. Feed `SessionTrajectory` recurrence into scoring as a weak amplifier.
6. Add `post_recovery_recurrence`.
7. Replay-test all new behavior.
8. Measure false positives and visible guidance noise.

## 1. Failure Normalization Audit

Review how `failure_sig` is produced and consumed today:

- `normalize.py`
- `fingerprint.py`
- `events.py`
- `detectors.py::repeated_failure`
- `hooks.py` event construction
- existing repeated-failure tests

Output of the audit should be a short list of unstable parts, such as line
numbers, temp paths, ids, counters, tracebacks, timestamps, or tool-specific
noise that makes equivalent failures look new.

Do not rewrite normalization broadly unless a replay/test demonstrates the
need.

## 2. Hierarchical Canonical Failure Grouping

Add the smallest structure needed to group failures at more than one precision
level:

```text
exact failure_sig -> canonical failure group -> tool/error class
```

Expected behavior:

- Exact signatures remain available for current detectors.
- Canonical failure groups collapse superficial changes, especially line-number
  drift after patches.
- Tool/error class is fallback evidence only, not strong enough alone for a new
  recovery.

Prefer one new field on `ToolEvent` only if needed, for example
`failure_group`. Avoid a new ledger unless a detector cannot be expressed over
the existing bounded event window.

## 3. `same_failure_after_mutation`

Detect this shape:

```text
failure A -> landed mutation -> failure A' where A' belongs to the same
canonical failure group
```

Rules:

- Only count mutations where `mutation_landed` is true.
- Only count failures with a usable `failure_sig` or canonical group.
- Do not fire when there is measurable failure improvement.
- Do not fire on the first recurrence unless policy thresholds still keep it in
  WARN/RECOVER territory conservatively.

This detector should target the real blind spot, not become a generic
"changed code and still failing" rule.

## 4. Measurable Failure Improvement

Recognize improvement only when the output exposes structured evidence.
Examples:

- `N failed` becomes `M failed` with `M < N`.
- Error count decreases.
- A failing check changes from broad failure to narrower failure with a clear
  lower count.
- Exit status improves only if the tool output semantics make that meaningful.

If improvement is ambiguous, treat it as unknown, not progress.

Expected policy effect:

- Improvement suppresses `same_failure_after_mutation` for that event.
- Improvement may decay score slightly only if tests prove it does not hide
  loops.

## 5. SessionTrajectory Recurrence

Use existing session trajectory as weak context:

- recent failure groups recur across turn boundaries
- recent action families recur after a recovery
- carryover exists and no material progress has happened

This should amplify another active signal. It should not independently trigger
recovery in Phase 2A.

## 6. `post_recovery_recurrence`

Detect when the model receives recovery guidance and then returns to the same
canonical failure/action pattern.

Rules:

- Stronger than ordinary recurrence because the model was explicitly warned.
- Must compare against evidence after the recovery checkpoint.
- Must avoid repeating the same visible guidance block when the canonical cause
  has not changed.

Visible output should be budgeted. Metrics/debug can count every recurrence;
the model should not see the same recovery text five times.

## Tests

Add replay-style tests for:

- same failure after one landed mutation
- same canonical failure with line-number drift
- same tool/error class but different canonical failure, should not recover by
  itself
- landed mutation followed by measurable improvement, should not fire
- landed mutation followed by different failure, should not fire
- post-recovery recurrence escalates
- repeated post-recovery recurrence does not spam identical guidance
- session trajectory recurrence amplifies but does not trigger alone

## Metrics

Add only counters that answer evaluation questions:

- `canonical_failure_matches`
- `same_failure_after_mutation`
- `failure_improvements`
- `post_recovery_recurrences`
- `suppressed_duplicate_recoveries`, if duplicate guidance suppression is added

## Stop Condition

Stop after implementation and replay tests. Do not add goal awareness or a
semantic evaluator in this phase.
