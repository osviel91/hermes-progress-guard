"""Deterministic detectors (handoff §10). Pure functions over ToolEvents.

Each detector is windowed, cheap (O(window) scans over fingerprints) and
returns a magnitude the policy can escalate on — not a boolean. Polling tools
are exempt from identical_result; cycles require >= 2 distinct tools so plain
same-tool polling never registers as a cycle.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Sequence

from .events import ToolEvent


def exact_repeat(events: Sequence[ToolEvent], cfg: Any) -> int:
    """Longest consecutive run of identical (tool, args_fingerprint).

    Poll tools (``process``, ``*_poll``, ``*_get_result``) are exempt: they
    are legitimately re-invoked with identical args, so a poll between calls
    breaks the run instead of counting toward it (handoff §15).
    """
    if not cfg.enabled:
        return 0
    best, run, prev = 0, 0, None
    for e in events:
        if e.is_poll:
            run, prev = 0, None
            continue
        key = (e.tool_name, e.args_fingerprint)
        run = run + 1 if key == prev else 1
        prev = key
        if run > best:
            best = run
    return best


def identical_result(events: Sequence[ToolEvent], cfg: Any) -> int:
    """Max number of *different* actions producing one result fingerprint.

    ``search(A) -> R, search(B) -> R`` counts 2 actions for R. Same-action
    repeats are exact_repeat's job, so same (tool, args) only counts once.
    """
    if not cfg.enabled:
        return 0
    actions_per_result: Dict[str, set] = defaultdict(set)
    for e in events:
        if e.is_poll or e.status != "ok":
            continue
        actions_per_result[e.result_fingerprint].add(
            (e.tool_name, e.args_fingerprint)
        )
    return max((len(v) for v in actions_per_result.values()), default=0)


def cycle(events: Sequence[ToolEvent], cfg: Any) -> bool:
    """Short periodic patterns in the tool-name sequence (A B A B, A B C A B C).

    Checks periods 2..max_cycle_length: the window tail must consist of
    ``repetitions`` identical blocks whose period contains >= 2 distinct tools.
    """
    if not cfg.enabled:
        return False
    names = [e.tool_name for e in events]
    window = names[-cfg.window:] if len(names) > cfg.window else names
    for length in range(2, cfg.max_cycle_length + 1):
        span = length * cfg.repetitions
        if len(window) < span:
            continue
        tail = window[-span:]
        base = tail[:length]
        if len(set(base)) < 2:
            continue
        if all(
            tail[i * length:(i + 1) * length] == base
            for i in range(cfg.repetitions)
        ):
            return True
    return False


def repeated_failure(events: Sequence[ToolEvent], cfg: Any) -> int:
    """Max count of events sharing the same failure signature.

    ``failure_sig`` (set on the event) folds in a de-noised excerpt of the
    actual result when Hermes only provides a generic error message, so
    genuinely different failures don't merge into one class. Falls back to
    ``error_class`` when the sig wasn't computed.
    """
    if not cfg.enabled:
        return 0
    counts = defaultdict(int)
    for e in events:
        if e.status != "error":
            continue
        key = (e.tool_name, e.failure_sig or e.error_class or "")
        if not key[1]:
            continue
        counts[key] += 1
    return max(counts.values(), default=0)


def repeated_thinking(segments: Sequence[str], threshold: int) -> int:
    """Longest consecutive run of identical reasoning segments.

    Segments are the completed, stripped lines of the ``kind="reasoning"``
    stream (fed from ``on_stream_delta``). A pure thinking loop repeats the
    same block verbatim dozens of times; consecutive identical segments is
    the cheap deterministic signal (no embeddings).
    """
    best, run, prev = 0, 0, None
    for s in segments:
        s = s.strip()
        if not s:
            continue
        run = run + 1 if s == prev else 1
        prev = s
        if run > best:
            best = run
    return best


def evaluate(events: Sequence[ToolEvent], cfg: Any) -> Dict[str, Any]:
    """All signals at once; the hooks pass the result to the policy."""
    return {
        "exact_repeat": exact_repeat(events, cfg.exact_repeat),
        "identical_result": identical_result(events, cfg.identical_result),
        "cycle": cycle(events, cfg.cycle),
        "repeated_failure": repeated_failure(events, cfg.repeated_failure),
    }
