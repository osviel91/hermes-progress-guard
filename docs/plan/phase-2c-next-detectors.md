# Phase 2C: Next Detectors

Status: candidate

## Goal

Choose the next smallest detector only after Phase 2A has replay data and Phase
2B has reviewed false positives. Do not implement this phase speculatively.

## Candidate Order

1. Improve canonical failure grouping if equivalent failures still escape.
2. Increase `SessionTrajectory` scoring weight if Phase 2A shows it is safe.
3. Add a narrower "recovery ignored" detector if `post_recovery_recurrence`
   misses obvious repeats.
4. Add `no_material_progress_window` only as a secondary signal paired with a
   concrete recurrence detector.
5. Consider manual task hints only if goal-agnostic rules cannot distinguish
   legitimate exploration from mutation-required work.
6. Keep a semantic evaluator out until deterministic signals are exhausted.

## Still Deferred

- generic `analysis_loop`
- generic `tool_churn`
- autonomous goal inference
- LLM critic
- critic-driven tool choices
- hard blocking based on a single new Phase 2 signal

## Entry Criteria

Start this phase only if Phase 2B shows one of these:

- Phase 2A misses recurring failures that can still be grouped
  deterministically.
- Phase 2A is correct but too weak to trigger recovery soon enough.
- Recovery guidance is ignored in a way the current recurrence detector cannot
  capture.
- Real sessions show no-material-progress drift with concrete recurrence
  evidence.

## Stop Condition

Implement at most one candidate, replay-test it, then evaluate again. Do not
bundle multiple speculative detectors.
