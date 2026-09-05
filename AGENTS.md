# Agent Notes

## Sources Of Truth

- Current baseline: Phase 2A, plugin version `0.3.0`, 122 tests passing.
- `PLAN.md` is the original Phase 1 plan and Hermes hook audit; use
  `docs/plan/` for current/future phased work.
- Active forward plans: `docs/plan/phase-2a-safe-failure-recurrence.md`,
  `docs/plan/phase-2b-evaluate-results.md`,
  `docs/plan/phase-2c-next-detectors.md`.
- There is no `pyproject.toml`, `pytest.ini`, lockfile, CI workflow, or local
  OpenCode config; do not invent toolchain commands from absent config.

## Commands

- Full verification: `.venv/bin/python -m pytest tests/ -q`.
- Focused verification: `.venv/bin/python -m pytest tests/test_policy.py -q`
  or the specific `tests/test_*.py` touched.
- Tests import the hyphenated plugin directory through `conftest.py` as
  `hermes_plugins.progress_guard`; keep relative imports inside the plugin.

## Runtime Shape

- Plugin entrypoint is `plugins/progress-guard/__init__.py::register(ctx)`;
  implementation is wired in `hooks.py::ProgressGuard.install`.
- Registered hooks: `pre_tool_call`, `post_tool_call`, `transform_tool_result`,
  `on_stream_delta`, `on_session_start`, `on_session_end`,
  `on_session_finalize`, `on_session_reset`.
- Scoring happens in `post_tool_call` on the raw result before any
  `transform_tool_result` plugin, including `tool-output-compactor`.
- Recovery guidance is best-effort through `transform_tool_result`; Hermes uses
  first-valid-string-wins, so another transform plugin can hide it. Hard stops
  still work because they happen in `pre_tool_call`.

## Invariants

- `ToolEvent`, `TurnState`, and `SessionTrajectory` must stay bounded and must
  not store full raw tool results.
- Changed output is novelty, not material progress.
- Successful mutation is material progress only when Hermes/fallback
  `file_mutation_result_landed` confirms it landed.
- `steps_since_material_progress` only amplifies existing evidence; it must not
  trigger recovery by itself.
- Recovery delivery checkpoints the detector window and resets `stall_score`.
- Defaults intentionally prefer false negatives over false positives.
- `hermes_compat.py` must keep conservative local fallbacks because Hermes
  internals may be absent or move.

## Phase 2 Guardrails

- Phase 2 stays goal-agnostic until the plans/results say otherwise.
- First target is recurring failure after landed mutations:
  `same_failure_after_mutation`, canonical failure grouping, measurable failure
  improvement, weak `SessionTrajectory` amplification, and
  `post_recovery_recurrence`.
- Still deferred: generic `analysis_loop`, generic `tool_churn`, standalone
  `no_material_progress_window`, task hints/goal awareness, semantic evaluator,
  and more aggressive blocking.
- Metrics/debug may count every repeated pattern, but visible recovery guidance
  should avoid spam; summarize or budget repeated identical recovery causes.

## Change Rules

- For detector or policy changes, add the smallest replay-style test that fails
  without the new logic.
- Prefer extending existing event/state/detector/policy structures over adding a
  new ledger.
- Do not loosen mutation/material-progress semantics without tests.
- Do not add parsing dependencies for cases a few regexes can cover.
