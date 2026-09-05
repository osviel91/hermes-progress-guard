"""Deterministic detectors (handoff §10). Pure functions over ToolEvents.

Each detector is windowed, cheap (O(window) scans over fingerprints) and
returns a magnitude the policy can escalate on — not a boolean. Polling tools
are exempt from identical_result; cycles require >= 2 distinct entries so plain
same-tool polling never registers as a cycle, and any period containing a
mutating tool is treated as iterative development (progress), not a loop.

Cycles run on two levels (handoff §8): the exact tool-name trajectory and the
action-family trajectory (READ/SEARCH/...), so read_file -> grep -> search_files
oscillation is visible even when the concrete tools vary.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Sequence

from .events import ToolEvent


def exact_repeat(events: Sequence[ToolEvent], cfg: Any) -> int:
    """Length of the identical (tool, args_fingerprint) run ending at the tail.

    Only the run the *current* event is part of counts: an old burst of
    duplicates that stopped being extended must not keep poisoning later
    material progress (a real-session over-block seen in evaluation). Poll
    tools (``process_manage``, ``*_poll``, ``*_get_result``) are exempt:
    legitimately re-invoked with identical args, a poll between calls breaks
    the run instead of counting toward it (handoff §15).
    """
    if not cfg.enabled:
        return 0
    run, prev = 0, None
    for e in events:
        if e.is_poll:
            run, prev = 0, None
            continue
        key = (e.tool_name, e.args_fingerprint)
        run = run + 1 if key == prev else 1
        prev = key
    return run


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


def find_cycle(
    events: Sequence[ToolEvent],
    cfg: Any,
    key_fn: Callable[[ToolEvent], Any],
) -> int:
    """Smallest periodic block length (>=2) at the tail of the event window.

    The tail must consist of ``repetitions`` identical blocks whose period has
    >= 2 distinct keys, and no event inside the period may be mutating
    (write/run iteration is development progress, not a loop — handoff §16).
    Returns the period length, or 0 when no cycle is found.
    """
    if not cfg.enabled:
        return 0
    keys = [key_fn(e) for e in events]
    window = keys[-cfg.window:] if len(keys) > cfg.window else keys
    for length in range(2, cfg.max_cycle_length + 1):
        span = length * cfg.repetitions
        if len(window) < span:
            continue
        tail = window[-span:]
        base = tail[:length]
        if len(set(base)) < 2:
            continue
        if any(e.is_mutation for e in events[-len(window):][-span:]):
            continue
        if all(
            tail[i * length:(i + 1) * length] == base
            for i in range(cfg.repetitions)
        ):
            return length
    return 0


def cycle(events: Sequence[ToolEvent], cfg: Any) -> bool:
    """Short periodic patterns in the exact tool-name trajectory."""
    return bool(find_cycle(events, cfg, lambda e: e.tool_name))


def family_cycle(events: Sequence[ToolEvent], cfg: Any) -> int:
    """Cycle length over the action-family trajectory (READ/SEARCH/...).

    Catches read_file -> grep -> search_files oscillation that a concrete
    tool-name cycle misses. Returns 0 when no family cycle is detected.
    """
    return find_cycle(events, cfg, lambda e: e.family)


def canonical_matches(events: Sequence[ToolEvent]) -> int:
    """Max number of events sharing one canonical action key.

    Informational only (semantic-lite): two queries that differ only in word
    order or casing map to the same canonical key. Never used to score by
    itself — novelty is not stagnation.
    """
    counts: Dict[str, int] = defaultdict(int)
    for e in events:
        if e.status != "ok" or not e.canonical_action:
            continue
        counts[e.canonical_action] += 1
    return max(counts.values(), default=0)


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


def _failure_key(e: ToolEvent, *, include_error_class: bool = True) -> str:
    if e.failure_group or e.failure_sig:
        return e.failure_group or e.failure_sig or ""
    return e.error_class or "" if include_error_class else ""


def failure_improvement(events: Sequence[ToolEvent]) -> bool:
    """Tail failure has a lower structured count than the same pre-mutation failure."""
    if not events or events[-1].status != "error":
        return False
    cur = events[-1]
    key = _failure_key(cur, include_error_class=False)
    if not key or cur.failure_count is None:
        return False
    for i in range(len(events) - 2, -1, -1):
        if events[i].mutation_landed:
            for prev in reversed(events[:i]):
                if (
                    prev.status == "error"
                    and _failure_key(prev, include_error_class=False) == key
                    and prev.failure_count is not None
                ):
                    return cur.failure_count < prev.failure_count
            return False
    return False


def same_failure_after_mutation(events: Sequence[ToolEvent]) -> int:
    """Count same canonical failures after a landed mutation, unless improving."""
    if not events or events[-1].status != "error" or failure_improvement(events):
        return 0
    cur = events[-1]
    key = _failure_key(cur, include_error_class=False)
    if not key:
        return 0
    last_mut = -1
    for i in range(len(events) - 2, -1, -1):
        if events[i].mutation_landed:
            last_mut = i
            break
    if last_mut < 1:
        return 0
    if not any(
        e.status == "error" and _failure_key(e, include_error_class=False) == key
        for e in events[:last_mut]
    ):
        return 0
    return sum(
        1 for e in events[last_mut + 1:]
        if e.status == "error" and _failure_key(e, include_error_class=False) == key
    )


_WS = re.compile(r"\s+")


def normalize_segment(segment: str) -> str:
    """Cheap normalization for a reasoning block: whitespace + casing."""
    return _WS.sub(" ", (segment or "").strip()).lower()


def repeated_thinking(segments: Sequence[str], threshold: int) -> int:
    """Longest consecutive run of identical normalized reasoning segments.

    A pure thinking loop repeats the same block verbatim dozens of times;
    consecutive identical segments is the cheap deterministic signal (no
    embeddings).
    """
    best, run, prev = 0, 0, None
    for s in segments:
        s = normalize_segment(s)
        if not s:
            continue
        run = run + 1 if s == prev else 1
        prev = s
        if run > best:
            best = run
    return best


def reasoning_cycle(
    segments: Sequence[str], repetitions: int, max_cycle_length: int = 3
) -> int:
    """ABAB / ABCABC period over normalized reasoning segments (handoff §16).

    Returns the period length (>=2) when the tail repeats ``repetitions``
    times with >= 2 distinct normalized blocks, else 0.
    """
    norm = [normalize_segment(s) for s in segments]
    norm = [s for s in norm if s]
    if len(norm) < 2 * repetitions:
        return 0
    for length in range(2, max_cycle_length + 1):
        span = length * repetitions
        if len(norm) < span:
            continue
        tail = norm[-span:]
        base = tail[:length]
        if len(set(base)) < 2:
            continue
        if all(
            tail[i * length:(i + 1) * length] == base
            for i in range(repetitions)
        ):
            return length
    return 0


def evaluate(events: Sequence[ToolEvent], cfg: Any) -> Dict[str, Any]:
    """All signals at once; the hooks pass the result to the policy."""
    return {
        "exact_repeat": exact_repeat(events, cfg.exact_repeat),
        "identical_result": identical_result(events, cfg.identical_result),
        "cycle": cycle(events, cfg.cycle),
        "family_cycle": family_cycle(events, cfg.family_cycle),
        "canonical_matches": canonical_matches(events),
        "repeated_failure": repeated_failure(events, cfg.repeated_failure),
        "failure_improvement": failure_improvement(events),
        "same_failure_after_mutation": same_failure_after_mutation(events),
    }
