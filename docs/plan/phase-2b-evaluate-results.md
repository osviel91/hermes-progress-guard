# Phase 2B: Evaluate Results

Status: planned

## Goal

Decide whether Phase 2A is safe enough to keep enabled by default, tune down,
or revise. This phase measures behavior; it should not add new detectors.

## Inputs

- Unit tests added in Phase 2A.
- Replay tests from synthetic trajectories.
- Replay tests from real Hermes sessions when available.
- Debug/metric counters from representative runs.

## Evaluation Questions

1. Did `same_failure_after_mutation` catch the known blind spot?
2. Did canonical failure grouping collapse only superficial differences?
3. Did measurable improvement prevent false recovery on legitimate progress?
4. Did `SessionTrajectory` recurrence help without triggering alone?
5. Did `post_recovery_recurrence` catch ignored guidance?
6. Did visible recovery guidance become repetitive or noisy?
7. Did any new signal push sessions to hard stop too early?

## False Positive Review

Review these cases explicitly:

- multiple patches where tests still fail but failure count decreases
- same command failing for a genuinely different reason
- long debugging sessions with many reads/searches but no mutation
- legitimate retry after external/environmental failure
- post-recovery strategy change that still hits a related but improved failure

Bias remains false negatives over false positives.

## Noise Review

Measure visible friction separately from detector correctness:

- number of recovery messages injected per session
- number of recovery messages with the same canonical failure group
- number of post-recovery recurrences counted only in metrics/debug
- whether a shorter summary would have been enough

The Process Guard session review is the reference constraint: audit trails are
useful, repeated identical annotations are noise.

## Decisions

At the end of this phase, pick one outcome per new behavior:

- keep enabled by default
- keep enabled but lower weight
- keep as debug/metric-only
- narrow the canonical grouping rule
- disable or remove

Do not proceed to broader drift detection until this review is complete.
