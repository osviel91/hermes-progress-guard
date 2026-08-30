# Hermes Progress Guard — Implementation Plan (Phase 1)

Status: planning (grounded in Hermes-Agent commit `4f22543`, 2026-08-30)
Author: handoff from prior agent + analysis of actual Hermes source

## 0. Executive summary

Build Progress Guard as a **Hermes Python plugin** (`plugins/progress-guard/`),
not a fork and not a shell-hook. It watches the tool-call stream through three
official hooks (`post_tool_call`, `transform_tool_result`, `pre_tool_call`),
maintains in-memory per-`(session_id, turn_id)` state, computes a deterministic
stagnation score from four detectors (exact repeat, identical result, cycle,
repeated failure), and — when the score crosses thresholds — injects a guided
replan message and, after a bounded recovery budget, blocks the looping call.

It is a **complementary second layer**: Hermes already ships a
`ToolCallGuardrailController` (`agent/tool_guardrails.py`) that catches
same-call repetition; Progress Guard targets the patterns that controller
cannot see (cycles, identical results across different args, error-class
normalized failures) and adds the recovery narrative + budget that core lacks.

No second LLM, no embeddings, no core modification, O(1)-ish per call.

## 1. Ground truth: Hermes extension points (verified in source)

All references are to commit `4f22543` in `NousResearch/Hermes-Agent`.

### 1.1 Plugin model
- A plugin is a directory with a `plugin.yaml` manifest and an `__init__.py`
  exposing `register(ctx)`.
- Discovered from: bundled `<repo>/plugins/<name>/`, user `~/.hermes/plugins/<name>/`,
  and project `./.hermes/plugins/<name>/` (project requires
  `HERMES_ENABLE_PROJECT_PLUGINS=1`) — `hermes_cli/plugins.py`
  `_collect_directory_manifests()` (line 4541).
- **Opt-in**: standalone/user plugins load only when listed under
  `plugins.enabled` (line 4464); `hermes plugins enable <name>` exists.
  Plugins can be disabled the same way — satisfies handoff §29
  ("Plugin can be disabled via configuration").
- Config: `ctx.get_config("key", default)` reads
  `plugins.entries.<plugin_id>.settings.<key>` from config.yaml (line 1492).
- Register hooks with `ctx.register_hook("pre_tool_call", fn)` (line 3387).
  Canonical example: `plugins/security-guidance/__init__.py` (registers
  `pre_tool_call` + `transform_tool_result`).

### 1.2 `pre_tool_call` — the only blocking hook for plugins
- Fired in `agent/tool_executor.py:649` via
  `_dispatch_pre_tool_call_hooks(...)`, **before** the built-in
  `agent._tool_guardrails.before_call` (line 675).
- A callback may return `{"action": "block", "message": "..."}`. The tool call
  is vetoed and the model receives a synthetic result
  `{"error": "<message>"}` with `error_type="plugin_block"`; a `post_tool_call`
  fires with `status="blocked"` (tool_executor.py:681-707).
- Also supported: `{"action": "modify", "args": {...}}` (shallow-merge into
  args) and `{"action": "approve", ...}` (escalate to human approval).
- Kwargs: `tool_name, args, task_id, session_id, tool_call_id, turn_id,
  api_request_id, middleware_trace`.
- **Plugin block is our hard-stop + recovery-block mechanism.**

### 1.3 `post_tool_call` — reliable all-outcome observer
- Fires for **every** outcome (ok / error / blocked / cancelled):
  `_emit_terminal_post_tool_call` (tool_executor.py:300) and
  `_emit_post_tool_call_hook` (model_tools.py:1184).
- Kwargs: `tool_name, args, result, task_id, session_id, tool_call_id,
  turn_id, api_request_id, duration_ms, status, error_type, error_message,
  middleware_trace`.
- **Observer-only** (return values ignored); timeout-bounded and fail-open
  (`_HOOK_TIMEOUT_BOUNDED_HOOKS` includes `post_tool_call`).
- **This is our record + score point.** We hash the result immediately and
  never retain full payloads.

> Note: on the main executor path `handle_function_call` is invoked with
> `suppress_post_tool_call_hook()` + `skip_pre_tool_call_hook=True`; the
> executor re-fires both. Plugins don't care — both hooks still fire, just
> from `tool_executor.py` rather than `model_tools.py`.

### 1.4 `transform_tool_result` — the result-injection seam
- Fired in `model_tools.py:1605`, **after** `post_tool_call`, **before** the
  result is appended back into conversation context.
- Returning a string replaces the result; **first valid string wins**
  (registration order). Non-string returns ignored. Fail-open.
- Kwargs: same as `post_tool_call` minus `middleware_trace`.
- Fires only for executed tools (blocked calls return before this point).
- **This is our recovery-guidance injection point** (append the replan
  message to the result the model sees next).

### 1.5 Session lifecycle
- `on_session_start`, `on_session_end` (fires from `agent/turn_finalizer.py`
  with `task_id, turn_id, completed, interrupted, model, platform`),
  `on_session_finalize`, `on_session_reset`.
- **`on_session_reset` / `on_session_end` → our state cleanup point.**

## 2. Gap analysis: what Hermes already does vs. what we add

`agent/tool_guardrails.py` (`ToolCallGuardrailController`, wired in
`run_agent.py`) already covers, per turn:

| Built-in signal | Mechanism |
|---|---|
| Exact repeated failure (same tool+args+error) | warn/block on count (`exact_failure_*`) |
| Same-tool failure streak | warn/halt (`same_tool_failure_*`) |
| Idempotent no-progress: same read-only call, same result | warn/block (`no_progress_*`) |
| Consecutive identical call+result | loop-breaker notice + result-reference stub (`observe_call`) |
| Runaway caps | web_search ≤ 50, subagents ≤ 50 per turn |
| Tool classification | `IDEMPOTENT_TOOL_NAMES`, `MUTATING_TOOL_NAMES`, poller exemption (`is_stall_guard_repeatable`) |

**Gaps that Progress Guard uniquely fills:**

1. **Cycles** (A B A B / A B C A B C). The built-in identical-streak tracker
   resets on *any* different call, so alternating loops are invisible to it.
2. **Identical result across different args** (`search(A) → R`,
   `search(B) → R`). Built-in `no_progress` keys on `(tool, args_hash)`;
   different args never match.
3. **Error-class normalized repeated failure** (line numbers, IDs, messages
   stripped). Built-in `same_tool_failure` counts *tool* failures but not a
   normalized error class.
4. **Volatile-field normalization** (`timestamp`, `request_id`, `trace_id`,
   pagination cursors) before fingerprinting. Built-in `canonical_tool_args`
   sorts keys but never drops fields.
5. **Composite stagnation score + guided recovery + recovery budget + hard
   stop escalation.** Core only warns/halts per-signal; no replan narrative,
   no bounded recovery, no post-stall observability taxonomy.

**Reuse, don't duplicate:**
- `IDEMPOTENT_TOOL_NAMES` / `MUTATING_TOOL_NAMES` frozensets (handoff §16) —
  import from `agent.tool_guardrails` instead of maintaining a second list.
- `canonical_tool_args()`, `_sha256`-style hashing, poller exemption —
  reuse after normalization.
- Do **not** re-implement exact-failure / same-tool / no-progress /
  identical-streak detection in the plugin. Those are core-owned and already
  fire before us. Our `exact_repeat` detector exists to feed the *score* and
  is defaulted conservatively so the two layers don't double-shout.

## 3. Architecture decision

```
Hermes Agent (unchanged)
   │
   ├─ agent.tool_guardrails (built-in layer 1: same-call repetition, caps)
   │
   └─ progress-guard plugin (layer 2: trajectory stagnation)
        ├─ post_tool_call         → record event, normalize, fingerprint,
        │                            update detectors, compute score
        ├─ transform_tool_result  → append recovery/replan guidance to result
        ├─ pre_tool_call          → block when block_score reached or
        │                            recovery budget exhausted
        └─ on_session_end/reset   → drop per-(session,turn) state
```

- **Why a plugin and not shell hooks:** shell hooks are a subprocess per call
  (slow, no in-process state). The handoff's `{action: block, message}` wire
  shape maps 1:1 onto the plugin `pre_tool_call` return contract.
- **Why not a fork:** every capability below exists through public hooks.
  Verified: blocking, result injection, per-call observation, session cleanup.
- **State:** in-memory `dict[(session_id, turn_id)] -> TurnState`; explicit
  cleanup on `on_session_end` / `on_session_reset`; nothing persisted, nothing
  cross-session. Satisfies handoff §7.
- **Deterministic and cheap:** hashing + small integer counters only. No LLM,
  no embeddings, no cosine similarity. Satisfies handoff §6, §8, §9, §11.

### Known limitation to record
- `transform_tool_result` is **first-valid-string-wins**. If another plugin
  (e.g. `security-guidance`) returns a replacement and registers first, our
  appended guidance is dropped. Mitigation: the *recovery* message is best
  effort; the *hard stop* path goes through `pre_tool_call` block (unaffected
  by this ordering), and the model still sees our block message as the tool
  result. Not worth fighting for in MVP.

## 4. Module layout (adapted from handoff §5, kept lean)

```
plugins/progress-guard/
├── plugin.yaml              # name, version, hooks: [pre_tool_call, post_tool_call, transform_tool_result, on_session_reset, on_session_end]
├── __init__.py              # register(ctx): wire hooks, read config via ctx.get_config
├── config.py                # dataclass with conservative defaults; overridable from plugin settings
├── events.py                # ToolEvent dataclass (handoff §6): fingerprints + status + error_class, never raw payloads
├── normalize.py             # strip ignored_fields (configurable, per-tool later); reuse canonical_tool_args
├── fingerprint.py           # action_fingerprint(tool,args) / result_fingerprint(result); sha256 of canonical JSON
├── state.py                 # TurnState + per-(session,turn) registry + cleanup
├── detectors.py             # exact_repeat, identical_result, cycle, repeated_failure, stagnation (single module, ~5 small funcs)
├── policy.py                # score weights + CONTINUE/WARN/RECOVER/BLOCK decision
├── recovery.py              # replan message templates + recovery budget accounting
├── metrics.py               # counters: loops/cycles/repeated_results/repeated_failures/recoveries/hard_stops
├── debug.py                 # [progress-guard] log line builder (handoff §18)
└── hooks.py                 # _on_post_tool_call / _on_transform_tool_result / _on_pre_tool_call / _on_session_end
```

(5 detectors collapse into one `detectors.py`; `normalize` + `fingerprint`
could merge if they stay tiny. Decided during implementation, not planned.)

## 5. Detectors (handoff §10) — all deterministic, windowed, conservative

1. **exact_repeat** — consecutive streak of identical `(tool, args_hash)`
   within `window`; flag at `threshold` (default 3). Overlaps the built-in
   loop-breaker; defaults kept high to avoid double-firing.
2. **identical_result** — count of events sharing the same `result_hash`
   produced by **different** actions within the window; flag at `threshold`
   (default 3). This is the `search(A)→R search(B)→R` case.
3. **cycle** — periodic subsequence in the *tool-name* sequence. For each
   candidate length 2..`max_cycle_length`, test whether the window tail shows
   ≥ `repetitions` (default 2) repeats. O(window × max_len), deterministic.
   No embeddings (handoff §10.3).
4. **repeated_failure** — group events by `(tool, error_class)` where
   `error_class` is the normalized `error_type`/error message (digits,
   hex ids, paths, timestamps stripped); flag at `threshold` (default 3).
5. **stagnation** — composite signal: any of the above firing feeds the score;
   not a binary decision by itself.

## 6. Policy / Progress Score (handoff §11, configurable)

```
exact repeated action       +2
identical result            +2
repeated failure            +1
cycle detected              +2
successful state mutation   -3   (tool ∈ MUTATING_TOOL_NAMES, status ok)
materially changed result   -2   (result_hash != previous)
```

- Thresholds: `warn_score: 3`, `recover_score: 5`, `block_score: 7`.
- Decision: 0–2 CONTINUE · 3–4 WARN · 5–6 RECOVER · 7+ BLOCK.
- Score is **window-based with decay on progress evidence**, so legitimate
  polling (changing result each call) never accumulates (handoff §15).
- `poll`/`*_get_result`/`*_poll` tools are exempt from `identical_result`
  via `is_stall_guard_repeatable` (reused from core).

## 7. Recovery (handoff §12–§14)

1. **WARN / RECOVER** → in `transform_tool_result`, append a guided replan
   message to the result string (evidence list + "do not repeat the blocked
   action or a trivial variation" + re-evaluate/summarize/identify/choose
   steps). Non-blocking; the model sees it on the next step.
2. On each RECOVER decision, `recovery_count += 1` for the turn.
3. **Block / hard stop** in `pre_tool_call`: when `score ≥ block_score` **or**
   `recovery_count > max_attempts` (default 2), return
   `{"action": "block", "message": <hard-stop message>}`. The model receives
   it as a synthetic error result and is directed to produce the final
   report: objective / progress achieved / blocker / strategies attempted /
   reason for stopping (no invented success).
4. **Why block ≠ turn halt is acceptable:** Hermes core has a `halt` concept
   but it is not exposed to plugins. A block result still reaches the model,
   which then produces the terminal summary — same outcome without touching
   core. If a true halt is later desired, that is an upstream contribution
   (see §11).

## 8. Config schema (handoff §19)

Lives under `plugins.entries.progress-guard.settings` in config.yaml (read via
`ctx.get_config`), mirroring the handoff defaults:

```yaml
plugins:
  enabled: [progress-guard]
  entries:
    progress-guard:
      settings:
        debug: false
        exact_repeat:   {enabled: true, window: 8, threshold: 3}
        identical_result: {enabled: true, threshold: 3}
        cycle:          {enabled: true, window: 10, max_cycle_length: 3, repetitions: 2}
        repeated_failure: {enabled: true, threshold: 3}
        policy:         {warn_score: 3, recover_score: 5, block_score: 7}
        recovery:       {max_attempts: 2}
        normalization:  {ignored_fields: [timestamp, request_id, trace_id]}
```

Defaults prefer false negatives over false positives (handoff §19).
Every detector can be switched off individually; the whole plugin can be
disabled by removing it from `plugins.enabled`.

## 9. Testing (handoff §20–§21)

- **Unit tests** (pure functions, pytest, no Hermes import needed):
  exact A A A → detect; A B C → no detect; A B A B → detect;
  A B C A B C → detect; A→X B→X C→X → detect; poll 10/30/70/done →
  no recovery; patch(A)/(B)/(C) mismatch → detect; recovery budget →
  hard stop after 2.
- **Integration tests** (simulate the agent loop by invoking the plugin's
  hook callables directly with synthetic kwargs — no full Hermes runtime):
  Scenario A exact loop → recover; B alternating loop → cycle+recover;
  C legitimate polling → continue; D recovery succeeds → 1 recovery, no hard
  stop; E recovery fails → hard stop.
- Detectors are pure functions over a list of `ToolEvent`s → trivially
  unit-testable without Hermes (this is the main reason for the split in §4).

## 10. Acceptance criteria mapping (handoff §29)

| Criterion | Where |
|---|---|
| Exact repeated calls detected | `detectors.exact_repeat` |
| Short cyclic patterns detected | `detectors.cycle` |
| Identical stagnant results detected | `detectors.identical_result` |
| Repeated equivalent failures detected | `detectors.repeated_failure` |
| Legit polling w/ changing results doesn't trigger | poller exemption + changed-result decay |
| Stall can interrupt strategy | `transform_tool_result` guidance |
| Model receives useful recovery context | `recovery.py` message templates |
| Recovery attempts bounded | `recovery.max_attempts` |
| Persistent stalls terminate safely | `pre_tool_call` block → final report |
| State isolated per turn/session | `state.py` keyed `(session_id, turn_id)` + cleanup |
| Plugin disable-able via config | `plugins.enabled` |
| Debug explains decisions | `debug.py` line |
| Unit tests per detector | §9 |
| Integration tests for recovery | §9 |
| No 2nd LLM / no embeddings | architecture |
| Hermes core unmodified | plugin-only |
| Existing guardrails still work | we never touch `run_agent` / `tool_guardrails` |

## 11. Upstream contribution candidates

1. **`transform_tool_result` first-wins ambiguity** — append-mode (concat
   when multiple plugins return strings) would let multiple result-enriching
   plugins coexist. Small, clearly useful.
2. **Plugin-visible turn-halt directive** — expose something like a
   `{"action": "halt", "message": ...}` for `pre_tool_call` that halts the
   turn (mirroring core's `ToolGuardrailDecision.halt`) instead of a
   synthetic block result. Currently the only plugin lever is `block`.
3. **Normalization hook for `canonical_tool_args`** — a built-in
   `ignored_fields` mechanism so volatile fields (timestamp/request_id) are
   stripped at the source rather than re-implemented in plugins.

## 12. Phase 2+ notes (handoff §23–§26, NOT in scope)

- Phase 2 semantic similarity, Phase 3 LLM judge, Phase 4 Completion Gate all
  depend on Phase 1 traces. No decisions made here beyond the handoff's
  layering rule: the four layers stay separate.
- `agent-completion-gate` remains a Phase 4 reference only.

## 13. Open decisions before implementation

1. Confirm thresholds (defaults above are conservative; tuning comes from
   real traces — handoff §22).
2. Whether `recovery_count` should be per-turn (proposed) or per-session.
   Handoff §7 says per-turn isolation; §14's "hard stop after N stalls" reads
   naturally per-turn.
3. Whether to merge `normalize.py` into `fingerprint.py` (implementation-time
   call).
