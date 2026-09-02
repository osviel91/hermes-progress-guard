"""Policy engine (handoff §11-§13): detector signals -> score delta + decision.

Progress evidence only counts when it is *material*: a mutation Hermes
confirmed landed (-3), or a poll that visibly advanced/completed (-2). Novelty
alone (different result, fresh query, new reasoning) is not progress and does
not decay the score. Steps since the last material progress amplify detector
evidence once past a threshold, but never trigger recovery by themselves —
legitimate exploration must not become a false stall.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .events import ToolEvent


def _detector_fired(signals: Dict[str, Any], cfg: Any) -> bool:
    er = cfg.exact_repeat
    ir = cfg.identical_result
    rf = cfg.repeated_failure
    return bool(
        (er.enabled and signals["exact_repeat"] >= er.threshold)
        or (ir.enabled and signals["identical_result"] >= ir.threshold)
        or (rf.enabled and signals["repeated_failure"] >= rf.threshold)
        or (cfg.cycle.enabled and signals["cycle"])
        or (cfg.family_cycle.enabled and signals["family_cycle"] >= 2)
    )


def score_delta(
    signals: Dict[str, Any], cfg: Any, event: Optional[ToolEvent] = None,
    prev_result_fingerprint: Optional[str] = None,
    steps_since_material_progress: Optional[int] = None,
) -> int:
    del prev_result_fingerprint  # novelty is no longer a decay signal
    er = cfg.exact_repeat
    ir = cfg.identical_result
    rf = cfg.repeated_failure
    fired = _detector_fired(signals, cfg)

    delta = 0

    # Material-progress decay only counts when no detector is firing.
    if event is not None and not fired and event.status == "ok":
        if event.material_progress:
            if event.mutation_landed:
                delta -= 3  # Hermes confirmed the file mutation landed
            else:
                delta -= 2  # poll visibly advanced/completed

    if fired:
        # Identical failing repetitions are already counted by
        # repeated_failure (+1 each); exact_repeat measures the same run of
        # identical calls, so stacking both would inflate escalation and skip
        # the guided RECOVER band (3 -> 9 in one step instead of passing 5/6).
        rf_firing = rf.enabled and signals["repeated_failure"] >= rf.threshold
        if er.enabled and not rf_firing and signals["exact_repeat"] >= er.threshold:
            delta += 2 * (signals["exact_repeat"] - er.threshold + 1)
        if ir.enabled and signals["identical_result"] >= ir.threshold:
            delta += 2 * (signals["identical_result"] - ir.threshold + 1)
        if rf.enabled and signals["repeated_failure"] >= rf.threshold:
            delta += 1 * (signals["repeated_failure"] - rf.threshold + 1)
        if cfg.cycle.enabled and signals["cycle"]:
            delta += 2
        if cfg.family_cycle.enabled and signals["family_cycle"] >= 2:
            delta += 2
        # steps-since-material-progress amplifies only existing detector
        # evidence (handoff §10): never fires recovery by itself. It applies
        # solely to spread evidence (identical_result: different actions, same
        # result) where a slow signal benefits from a nudge. Tight repeat runs
        # (exact_repeat), consecutive failing runs (repeated_failure) and
        # structural cycles (cycle/family_cycle) already escalate on every
        # event of the loop; a per-event +1 there skews the band edges (a WARN
        # 3 pushed to 4 makes the next run jump 4 -> 8 and skip the guided
        # RECOVER stage), so the bonus is excluded while they fire.
        ir_firing = ir.enabled and signals["identical_result"] >= ir.threshold
        rf_firing = rf.enabled and signals["repeated_failure"] >= rf.threshold
        er_any = er.enabled and signals["exact_repeat"] >= er.threshold
        cycle_active = (cfg.cycle.enabled and signals["cycle"]) or (
            cfg.family_cycle.enabled and signals["family_cycle"] >= 2
        )
        sc = cfg.steps
        if (
            sc.enabled
            and ir_firing
            and not er_any
            and not rf_firing
            and not cycle_active
            and steps_since_material_progress is not None
            and steps_since_material_progress >= sc.bonus_threshold
        ):
            delta += sc.bonus_delta

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
