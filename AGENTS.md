# Agent Notes

## Project

`hermes-progress-guard` is a Hermes Python plugin that adds deterministic
anti-stall / tool-loop protection on top of Hermes built-in guardrails. It is
not a fork, does not use a second LLM, and does not use embeddings.

Current baseline: Phase 1.6, plugin version `0.2.0`, with 110 tests passing via
`.venv/bin/python -m pytest tests/ -q`.

## Planning

- Use `docs/plan/` for project plans and check it before resuming phased work.
- `PLAN.md` is the original Phase 1 implementation plan and Hermes hook audit.
- Active forward plans live in:
  - `docs/plan/phase-2a-safe-failure-recurrence.md`
  - `docs/plan/phase-2b-evaluate-results.md`
  - `docs/plan/phase-2c-next-detectors.md`

## Architecture

Main code lives in `plugins/progress-guard/`:

- `hooks.py`: Hermes hook integration and record/score/decision flow.
- `events.py`: bounded tool-event metadata; never store full raw payloads.
- `state.py`: per-turn state plus rolling `SessionTrajectory`.
- `detectors.py`: deterministic repeat/failure/cycle/reasoning detectors.
- `policy.py`: score deltas and CONTINUE/WARN/RECOVER/BLOCK decisions.
- `material_progress.py`: conservative material-progress classification.
- `canonical.py`: semantic-lite action keys, no embeddings or NLP model.
- `families.py`: tool family classification.
- `normalize.py` and `fingerprint.py`: stable args/result/failure fingerprints.
- `recovery.py`: visible recovery and hard-stop messages.
- `hermes_compat.py`: imports Hermes guardrail/classification helpers with local
  fallbacks.
- `config.py`, `metrics.py`, `debug.py`: settings, counters, diagnostics.

Registered hooks: `pre_tool_call`, `post_tool_call`, `transform_tool_result`,
`on_stream_delta`, `on_session_start`, `on_session_end`,
`on_session_finalize`, `on_session_reset`.

## Behavior Model

Flow: observe raw tool result in `post_tool_call` -> classify family/canonical
action/mutation/poll/material progress -> run deterministic detectors -> update
policy score -> optionally inject recovery guidance in `transform_tool_result`
-> block through `pre_tool_call` only after budget/threshold exhaustion.

Important invariants:

- Raw results are analyzed before `tool-output-compactor` transforms output.
- Changed output is novelty, not material progress.
- Successful mutation is material progress only when Hermes/fallback confirms it
  landed.
- `steps_since_material_progress` amplifies existing evidence; it does not
  trigger recovery by itself.
- Recovery delivery checkpoints the detector window and resets stall score.
- Defaults should favor false negatives over false positives.

## Tests

Tests live in `tests/`:

- `test_detectors.py`
- `test_policy.py`
- `test_integration.py`
- `test_normalize.py`
- `test_families.py`
- `test_material_progress.py`
- `test_coexistence.py`

Run the suite with:

```bash
.venv/bin/python -m pytest tests/ -q
```

For detector/policy changes, add the smallest replay-style test that would fail
without the new logic.

## Phase 2 Direction

Phase 2 should stay goal-agnostic for now. The first safe target is recurring
failure after landed mutations, not broad trajectory drift.

Phase 2A scope:

- audit current failure signature normalization
- add hierarchical canonical failure grouping
- add `same_failure_after_mutation`
- detect measurable failure improvement where possible
- use `SessionTrajectory` recurrence only as a weak amplifier
- add `post_recovery_recurrence`
- replay-test all behavior
- measure false positives and visible guidance noise

Still deferred unless plans/results explicitly change:

- generic `analysis_loop`
- generic `tool_churn`
- standalone `no_material_progress_window` recovery trigger
- task hints / goal awareness
- semantic evaluator / LLM critic
- more aggressive blocking policy

## Coexistence Notes

`tool-output-compactor` also hooks `transform_tool_result`. Hermes currently
uses first-valid-string-wins transform semantics. Progress Guard detection is
safe because it scores raw results in `post_tool_call`; visible recovery text can
still lose to transform ordering if another plugin returns a string first. Hard
stops are unaffected because they happen in `pre_tool_call`.

Recent real-session plugin evaluation added one design constraint for this
repo: metrics/debug may count every repeated pattern, but visible guidance
should avoid spam. Repeated identical recovery causes should be summarized or
budgeted instead of injected verbatim over and over.

## Development Rules

- Keep changes deterministic, bounded, and cheap.
- Prefer extending existing `ToolEvent`, `TurnState`, `SessionTrajectory`, and
  detector/policy structures over adding a new ledger.
- Do not store full tool results in state.
- Do not add dependencies for parsing that a few regexes can handle.
- Do not loosen mutation/material-progress semantics without tests.
- Do not make a new signal trigger hard stop alone unless replay tests and the
  plan explicitly justify it.
- Preserve plugin compatibility with missing Hermes internals by keeping local
  fallbacks conservative.
