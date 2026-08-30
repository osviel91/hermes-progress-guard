"""Policy engine: turns detector signals into a stall score delta and a
decision (handoff §11). Weights are configurable in ``PolicyConfig``.

Evidence of progress (a successful mutation, or an ok result that materially
changed) pushes the score down; detector magnitudes push it up, scaled by how
far past threshold the magnitude is so persistent loops escalate to block.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .events import ToolEvent


def score_delta(
    signals: Dict[str, Any], cfg: Any, event: Optional[ToolEvent] = None,
    prev_result_fingerprint: Optional[str] = None,
) -> int:
    er = cfg.exact_repeat
    ir = cfg.identical_result
    rf = cfg.repeated_failure
    detector_fired = (
        (er.enabled and signals["exact_repeat"] >= er.threshold)
        or (ir.enabled and signals["identical_result"] >= ir.threshold)
        or (rf.enabled and signals["repeated_failure"] >= rf.threshold)
        or (cfg.cycle.enabled and signals["cycle"])
    )

    delta = 0

    # Progress evidence only counts when no detector is currently firing:
    # a cycle or repeat that merely changes its output every step (A B A B
    # with fresh results) must still accumulate, while genuine polling and
    # diverse work stay at zero (handoff §15).
    if event is not None and not detector_fired:
        changed = (
            prev_result_fingerprint is not None
            and event.result_fingerprint != prev_result_fingerprint
        )
        if event.is_mutation and event.status == "ok":
            delta -= 3  # successful state mutation = strong progress
        elif event.status == "ok" and changed:
            delta -= 2  # materially changed result = progress (covers polling)

    if detector_fired:
        if signals["exact_repeat"] >= er.threshold:
            delta += 2 * (signals["exact_repeat"] - er.threshold + 1)
        if signals["identical_result"] >= ir.threshold:
            delta += 2 * (signals["identical_result"] - ir.threshold + 1)
        if signals["repeated_failure"] >= rf.threshold:
            delta += 1 * (signals["repeated_failure"] - rf.threshold + 1)
        if signals["cycle"]:
            delta += 2

    return delta


def decide(score: int, cfg: Any) -> str:
    policy = cfg.policy
    if score >= policy.block_score:
        return "BLOCK"
    if score >= policy.recover_score:
        return "RECOVER"
    if score >= policy.warn_score:
        return "WARN"
    return "CONTINUE"
