# hermes-progress-guard

Deterministic anti-stall / tool-loop protection for Hermes Agent, delivered
as a Hermes Python plugin. Complements Hermes' built-in tool guardrails
(no fork, no second LLM, no embeddings).

**Planning:** see [PLAN.md](PLAN.md).

## Status

Phase 1.6 implemented and tested (107 tests passing, `.venv/bin/python -m
pytest tests/ -q`). Version `0.2.0`, registered hooks: `pre_tool_call`,
`post_tool_call`, `transform_tool_result`, `on_stream_delta`, and the session
lifecycle hooks `on_session_start` / `on_session_end` / `on_session_finalize`
/ `on_session_reset`.

### What changed in Phase 1.6

Progress Guard moved from a *loop detector* to a *deterministic
trajectory/convergence supervisor* over the gap Hermes leaves: "different
outputs ≠ progressing toward the objective". The core semantic split:

- **Successful mutation ≠ material progress.** A `write_file`/`patch` result
  only counts as progress when it provably *landed*
  (`file_mutation_result_landed`, reused from Hermes' public
  `agent.tool_result_classification`: `bytes_written` for `write_file`,
  `success: true` for `patch`, no top-level error). Bookkeeping mutations,
  plain `ok` terminal runs, fresh reads/searches and new reasoning are
  **novelty, never progress**.
- **Action families.** Each tool call is classified into an intent family
  (READ/SEARCH/POLL/MUTATE/EXECUTE/DELEGATE/COMMUNICATE/MEMORY/OTHER), so a
  `read_file → grep → read_file → search_files` oscillation is a cycle at the
  *intent* level even though no concrete tool ever repeats. The exact
  tool-name trajectory detector still runs alongside it.
- **steps_since_material_progress.** Actions since the last material-progress
  event are counted. By itself the counter never triggers recovery
  (exploration must keep working); once the threshold is exceeded it
  **amplifies detector evidence**, and it is surfaced in metrics, debug lines
  and recovery messages.
- **Novelty ≠ progress.** A changed result is recorded but does *not* erase
  accumulated stagnation evidence and does *not* decay the stall score.
- **Canonical (semantic-lite) action keys** collapse jitter
  (`grep -n foo file` ≈ `grep -i foo file`; `search Hermes tool loops` ≈
  `search tool loops Hermes`) with no embeddings and no NLP.
- **Reasoning-loop detection extended** to ABAB and ABCABC cycles over
  normalized reasoning blocks, alongside the existing AAA run detector.
- **Rolling session trajectory.** At each turn end (`on_session_end`), the
  turn's recent action families and failure signatures fold into a
  session-limited `SessionTrajectory`; real boundaries are `on_session_start`
  (fresh) and `on_session_finalize`/`on_session_reset` (teardown). Internal
  continuations therefore do not fully reset no-progress perception.
- **Hard-stop metric** `blocked_calls_after_hard_stop` counts tool attempts
  issued *after* a hard stop, giving evidence for a future upstream
  `action=halt` proposal.
- **tool-output-compactor coexistence** is covered by tests; see the
  compactor section below.

## Install in Hermes

1. Copy `plugins/progress-guard/` into `~/.hermes/plugins/` (a live copy is
   already deployed and runtime-validated against the installed Hermes).
2. Enable it and configure under `plugins.entries.progress-guard.settings`.
3. `hermes plugins enable progress-guard` (or list it under
   `plugins.enabled` in `config.yaml`).

### Configuration schema

All keys live under `plugins.entries.progress-guard.settings` and are
overridable with dotted keys; defaults favor false negatives:

```yaml
plugins:
  entries:
    progress-guard:
      settings:
        enabled: true
        debug: false
        exact_repeat:      {enabled: true, window: 8, threshold: 3}
        identical_result:  {enabled: true, threshold: 3}
        cycle:             {enabled: true, window: 10, max_cycle_length: 3, repetitions: 2}
        family_cycle:      {enabled: true, window: 10, max_cycle_length: 3, repetitions: 2}
        repeated_failure:  {enabled: true, threshold: 3}
        reasoning_loop:    {enabled: true, threshold: 3, cycle_repetitions: 2, max_cycle_length: 3}
        steps:             {enabled: true, bonus_threshold: 6, bonus_delta: 1}
        material_progress: {enabled: true}
        canonical:         {enabled: true}
        policy:            {warn_score: 3, recover_score: 5, block_score: 7}
        recovery:          {max_attempts: 2}
        normalization:     {ignored_fields: [timestamp, request_id, trace_id]}
```

Environment overrides: `PROGRESS_GUARD_ENABLED`, `PROGRESS_GUARD_DEBUG`,
`PROGRESS_GUARD_MAX_ATTEMPTS`, `PROGRESS_GUARD_RECOVER_SCORE`,
`PROGRESS_GUARD_BLOCK_SCORE`.

### Behavior model

1. **OBSERVE** every tool result on `post_tool_call` (raw result, before any
   `transform_tool_result` runs) → **CLASSIFY** family + canonical key +
   mutation/poll/landed → **DETECT STAGNATION** (repeat/failure/cycle
   detectors) → **DETECT MATERIAL PROGRESS** → **DECIDE**
   CONTINUE / WARN / RECOVER / BLOCK.
2. **RECOVER** injects a guided recovery message into the tool result it is
   attached to. The message names the detected pattern, steps since material
   progress, last known material progress, and which strategy must not be
   repeated. Recovery budget: `recovery.max_attempts` (default 2), then the
   escalation becomes a hard stop.
3. **BLOCK** (via `pre_tool_call`) refuses execution with a hard-stop
   message once the budget is exhausted or `block_score` is reached; later
   attempts keep counting under `blocked_calls_after_hard_stop`.

### Reasoning-loop detection (reasoning deltas)

Pure thinking loops that emit no tool calls require the Hermes global opt-in:

```yaml
plugins:
  stream_reasoning_deltas: true
```

Without it the `on_stream_delta` hook never receives reasoning text and this
detector is inert. Thresholds: `reasoning_loop.threshold` (AAA runs) and
`reasoning_loop.cycle_repetitions`/`max_cycle_length` (ABAB/ABCABC). Cycle
detection deliberately operates **within one generation** (the plugin clears
its segment buffer at each new `iteration`, matching Hermes semantics); a
single repetition per iteration is not treated as a loop.

### Coexistence with tool-output-compactor

Both plugins hook `transform_tool_result`. Hermes runs every callback in
registration order but keeps only the **first string** returned
(`model_tools.py`, first-valid-string-wins); registration order is plugin
load order (topological, alphabetical tiebreak), so `progress-guard` runs
before `tool-output-compactor` today. Consequences (all covered by tests):

- Progress Guard always analyzes the **raw** result (its fingerprints are
  taken on `post_tool_call`, which fires before any transformation), so
  compaction can never corrupt detection state.
- When a stalled tool result is compactable, whichever plugin is ordered
  first "wins": today the recovery message survives and that result is not
  compacted; if the compactor were ordered first, its compaction would win
  and the appended recovery message would be lost (documented limitation —
  no chaining exists on either side).
- The hard stop is unaffected: it is enforced in `pre_tool_call`, which runs
  before any result transformation.

### Metrics

Phase-1 metrics kept: `exact_repeats`, `repeated_results`,
`repeated_failures`, `cycles_detected`, `recoveries_triggered`, `blocks`,
`hard_stops`. New: `action_family_cycles`, `canonical_action_matches`,
`material_progress_events`, `reasoning_cycles`, `blocked_calls_after_hard_stop`,
`session_trajectory_carryovers`. Debug mode emits one line per tool call:
`[progress-guard] tool=… family=… steps_since=… material=… stall_score=…
decision=…` plus per-detector counters.

### Known limitations (Phase 1.6, documented in the analysis doc)

- `steps_since_material_progress` never triggers recovery by itself —
  conservative, favoring false negatives.
- Terminal/`execute_code` success alone is never material progress (no
  parseable completion proof); verification-driven progress rules are
  deferred.
- No hook kwarg distinguishes a new user turn from an internal continuation
  (`/goal`, auto-continue); every `run_conversation()` mints a fresh
  `turn_id`. The session trajectory is folded per turn under the *session*,
  which is the conservative middle ground — see the analysis doc §lifecycle.
- Reasoning ABAB/ABCABC detection is within-one-iteration only.
