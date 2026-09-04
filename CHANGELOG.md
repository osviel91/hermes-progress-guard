# Changelog

All notable changes to this project are documented here.

This project follows semantic versions from `plugins/progress-guard/plugin.yaml`.

## [0.2.0] - 2026-09-04

### Added

- Material-progress detection: successful tool calls and landed file mutations are no longer treated as equivalent. `write_file`/`patch` count as progress only when Hermes' `file_mutation_result_landed` semantics confirm they landed.
- `hermes_compat.py` adapter for Hermes guardrail/classification imports, with conservative local fallbacks when Hermes internals move.
- Action-family classification (`READ`, `SEARCH`, `POLL`, `MUTATE`, `EXECUTE`, `DELEGATE`, `COMMUNICATE`, `MEMORY`, `OTHER`) and family-cycle detection for intent-level loops across different concrete tools.
- Semantic-lite canonical action keys for reducing query/command jitter without embeddings or LLM judging.
- `steps_since_material_progress` tracking, exposed in debug lines, recovery messages and policy scoring.
- Rolling `SessionTrajectory` state folded at `on_session_end` and cleared on real session boundaries.
- Reasoning-cycle detection for ABAB/ABCABC patterns in streamed reasoning deltas, alongside the original repeated-thinking detector.
- SESSION SUMMARY debug log on `on_session_finalize` for post-session metric inspection.
- Metrics: `action_family_cycles`, `canonical_action_matches`, `material_progress_events`, `reasoning_cycles`, `blocked_calls_after_hard_stop`, `session_trajectory_carryovers`.
- Tests for material progress, action families, canonical actions, compactor coexistence, recovery checkpoints, hard-stop retries and real-session regressions.

### Changed

- Policy no longer decays stall score for novelty alone. Changed read/search output is not material progress.
- `steps_since_material_progress` only amplifies spread evidence (`identical_result` from different actions), not exact repeats, consecutive repeated failures or structural cycles.
- Recovery delivery now checkpoints the detector window and resets stall score, so a strategy change after guidance is not hard-blocked by pre-recovery evidence.
- Exact-repeat detection now uses tail-run semantics: only the identical call run ending at the newest event counts. A historical duplicate burst no longer poisons later distinct work.
- Repeated exact failures no longer double-count both `exact_repeat` and `repeated_failure` for the same run.
- Recovery messages include action-family pattern, steps since material progress and last known material progress.
- README now documents the real-session evaluation blind spot: same underlying bug with landed edits and shifting exact failure signatures can escape deterministic detection.

### Fixed

- False positives from landed mutation bursts followed by legitimate work.
- False positives after a recovery message when the model changed strategy and made progress.
- Hard-stop after a micro-stall was fixed and the model had already switched to a successful command.
- `material_progress_events` metric inflation; it now increments per material event instead of re-counting the running turn total.
- Pending recovery/thinking guidance now still surfaces when the original tool result is non-string.

### Validated

- Full test suite: 110 passing tests.
- Live Hermes runtime validation with `progress-guard` plus `tool-output-compactor` enabled.
- Deterministic positive-control driver: mutation-no-progress, action-family cycle and hard-stop-after-ignored-block scenarios.
- Real-session negative controls and follow-up regression tests for both over-blocks found during evaluation.

## [0.1.0] - 2026-08-31

### Added

- Initial Hermes `progress-guard` plugin.
- Hook integration for `pre_tool_call`, `post_tool_call`, `transform_tool_result`, `on_stream_delta`, `on_session_end` and `on_session_reset`.
- Deterministic stall detectors for exact repeated calls, identical stagnant results, tool-name cycles and repeated failure classes.
- Score-based policy with `WARN`, `RECOVER` and `BLOCK` thresholds.
- Guided recovery message injection and bounded hard-stop behavior.
- Normalization for tool args/results and denoised error/failure signatures.
- Metrics and debug logging for detected loops, repeated results, repeated failures, recoveries and hard stops.
- Tests covering detector behavior, normalization, policy decisions, integration flows and session cleanup.

### Changed

- Added reasoning-loop detection from streamed reasoning deltas.
- Added throttled reasoning-stream diagnostics for debugging.
- Repeated-failure signatures now group generic/empty error messages by denoised result output.
- Cycle detection skips periods containing mutating tools, preserving normal write/test iteration workflows.
- `None` error messages are treated as generic failure messages for signature grouping.

### Documented

- Repeated-failure normalization behavior.
- Phase 1 implementation plan and baseline plugin behavior.
