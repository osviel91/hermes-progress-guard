# hermes-progress-guard

Deterministic anti-stall / tool-loop protection for Hermes Agent, delivered
as a Hermes Python plugin. Complements Hermes' built-in tool guardrails
(no fork, no second LLM, no embeddings).

**Planning:** see [PLAN.md](PLAN.md) — grounded in Hermes-Agent commit `4f22543`.

## Status

Phase 1 MVP implemented and tested (49 tests passing).

- `plugins/progress-guard/` — the plugin (fingerprinting, exact repeat,
  identical result, cycle, repeated failure, **reasoning/thinking-loop
  detection**, stagnation score, guided recovery, recovery budget, hard stop,
  metrics, debug mode).
- `tests/` — unit tests per detector + integration scenarios A–E simulating
  the agent loop (hooks driven directly, no Hermes runtime required).

Run tests: `python -m pytest tests/ -q`

### Install in Hermes

1. Symlink/copy `plugins/progress-guard/` into `~/.hermes/plugins/`.
2. Enable it and configure under `plugins.entries.progress-guard.settings`
   (see `PLAN.md` §8 for the full schema; defaults are conservative).
3. `hermes plugins enable progress-guard`

### Thinking-loop detection (reasoning deltas)

The plugin also watches `kind="reasoning"` stream deltas to catch pure
thinking loops that emit no tool calls. This requires Hermes' global opt-in
in `config.yaml`:

```yaml
plugins:
  stream_reasoning_deltas: true
```

Without it, the hook never receives reasoning text and the detector is inert.
Threshold: `reasoning_loop.threshold` (default 3 consecutive identical
reasoning segments); disable via `reasoning_loop.enabled: false`.

### Repeated-failure detection (error-class normalization)

Hermes reports terminal/script failures with a generic `error_type='tool_error'`
and a generic message (`Script exited with code 1`), so the plugin groups
repeated failures by a **de-noised signature of the actual result output**
(line numbers, paths, hex IDs, digits stripped) whenever the message is
generic. Genuinely different failing commands therefore do **not** merge into
one class, while `patch(A)`/`patch(B)` with the same `context mismatch at
line N` still collapse correctly.

Out of scope for Phase 1 (planned later): semantic similarity, LLM judge,
Definition of Done / completion gate.


