# hermes-progress-guard

Deterministic anti-stall / tool-loop protection for Hermes Agent, delivered
as a Hermes Python plugin. Complements Hermes' built-in tool guardrails
(no fork, no second LLM, no embeddings).

**Planning:** see [PLAN.md](PLAN.md) — grounded in Hermes-Agent commit `4f22543`.

## Status

Phase 1 MVP implemented and tested (37 tests passing).

- `plugins/progress-guard/` — the plugin (fingerprinting, exact repeat,
  identical result, cycle, repeated failure, stagnation score, guided
  recovery, recovery budget, hard stop, metrics, debug mode).
- `tests/` — unit tests per detector + integration scenarios A–E simulating
  the agent loop (hooks driven directly, no Hermes runtime required).

Run tests: `python -m pytest tests/ -q`

### Install in Hermes

1. Symlink/copy `plugins/progress-guard/` into `~/.hermes/plugins/`.
2. Enable it and configure under `plugins.entries.progress-guard.settings`
   (see `PLAN.md` §8 for the full schema; defaults are conservative).
3. `hermes plugins enable progress-guard`

Out of scope for Phase 1 (planned later): semantic similarity, LLM judge,
Definition of Done / completion gate.

