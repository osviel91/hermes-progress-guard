"""Hook wiring for Progress Guard (handoff §13, §17, §18).

    post_tool_call          record event -> normalize -> fingerprint -> family
                            -> canonical key -> material-progress assessment
                            (raw result) -> update detectors -> score -> decide
    transform_tool_result   inject recovery/replan guidance into the result
                            the model sees next (only fires for executed calls)
    pre_tool_call           block when the score is past the block threshold or
                            the recovery budget is exhausted; counts post-block
                            attempts
    on_stream_delta         watch kind="reasoning" deltas for pure thinking
                            loops (identical repeats and ABAB/ABCABC cycles)
    on_session_start        brand-new session -> clean SessionTrajectory
    on_session_end          per-turn -> fold compact carryover, drop the turn
    on_session_finalize/reset  real session teardown -> drop session state

Material progress is always assessed on the *raw* post_tool_call result, i.e.
before tool-output-compactor could transform anything (handoff §19).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from . import hermes_compat as compat
from .canonical import canonical_action
from .config import ProgressGuardConfig
from .debug import debug_line
from .detectors import evaluate as detector_signals
from .detectors import reasoning_cycle, repeated_thinking
from .events import ToolEvent
from .families import classify_action
from .fingerprint import action_fingerprint, result_fingerprint
from .material_progress import MaterialProgress
from .material_progress import assess as assess_material
from .material_progress import poll_state
from .metrics import Metrics
from .normalize import (
    error_class,
    failure_count,
    failure_group,
    failure_signature,
    normalize_args,
    normalize_result,
)
from .policy import decide, score_delta
from .recovery import (
    family_trail,
    hard_stop_message,
    recovery_message,
    thinking_recovery_message,
)
from .state import StateRegistry

logger = logging.getLogger(__name__)


class ProgressGuard:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.cfg = ProgressGuardConfig.from_ctx(ctx)
        self.registry = StateRegistry()
        self.metrics = Metrics()

    def install(self, ctx: Any) -> None:
        ctx.register_hook("pre_tool_call", self.on_pre_tool_call)
        ctx.register_hook("post_tool_call", self.on_post_tool_call)
        ctx.register_hook("transform_tool_result", self.on_transform_tool_result)
        ctx.register_hook("on_stream_delta", self.on_stream_delta)
        ctx.register_hook("on_session_start", self.on_session_start)
        ctx.register_hook("on_session_end", self.on_session_end)
        ctx.register_hook("on_session_finalize", self.on_session_finalize)
        ctx.register_hook("on_session_reset", self.on_session_reset)

    # -- classification ----------------------------------------------------

    def _classify(self, tool_name: str) -> tuple:
        return (
            tool_name in compat.MUTATING_TOOL_NAMES,
            compat.is_stall_guard_repeatable(tool_name),
        )

    # -- record + score ----------------------------------------------------

    def on_post_tool_call(
        self,
        tool_name: str = "",
        args: Any = None,
        result: Any = None,
        session_id: str = "",
        turn_id: str = "",
        tool_call_id: str = "",
        status: Optional[str] = None,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        **_: Any,
    ) -> None:
        if not self.cfg.enabled:
            return
        state = self.registry.get(session_id, turn_id)
        if tool_call_id and state.pending_recovery and tool_call_id != state.pending_recovery:
            state.pending_recovery = None  # stale: recovery never materialized
            state.pending_recovery_cause = ""
        if status in (None, "blocked", "cancelled"):
            return  # artifact of our own block / user cancellation — not agent behavior

        try:
            afp = action_fingerprint(tool_name, normalize_args(args, self.cfg.ignored_fields))
            norm_result = normalize_result(result)
            rfp = result_fingerprint(norm_result)
        except Exception:
            return  # never let a fingerprinting bug take down the agent

        is_mutation, is_poll = self._classify(tool_name)
        family = classify_action(tool_name, args)
        canonical = (
            canonical_action(tool_name, family, args) if self.cfg.canonical.enabled else ""
        )

        # Material-progress assessment on the RAW result (before any compactor).
        raw_result = result if isinstance(result, str) else norm_result
        landed = (
            compat.file_mutation_result_landed(tool_name, raw_result)
            if self.cfg.material_progress.enabled
            else False
        )
        cur_pct, cur_done = (None, False)
        if family == "POLL" and status == "ok":
            cur_pct, cur_done = poll_state(raw_result)
        mp = MaterialProgress()
        if self.cfg.material_progress.enabled:
            mp = assess_material(
                tool_name=tool_name,
                result=raw_result,
                status=status or "ok",
                family=family,
                is_mutation=is_mutation,
                prev_poll_pct=state.last_poll_pct,
                prev_poll_done=state.last_poll_done,
                mutation_landed=landed,
            )

        fsig = failure_signature(error_type, error_message, norm_result) if status == "error" else None
        fgroup = failure_group(fsig) if fsig else None
        event = ToolEvent(
            tool_name=tool_name,
            args_fingerprint=afp,
            result_fingerprint=rfp,
            status=status or "ok",
            error_class=error_class(error_type, error_message) if status == "error" else None,
            failure_sig=fsig,
            failure_group=fgroup,
            failure_count=failure_count(error_message or "", norm_result) if status == "error" else None,
            is_mutation=is_mutation,
            is_poll=is_poll,
            tool_call_id=tool_call_id or "",
            family=family,
            canonical_action=canonical,
            mutation_landed=landed,
            material_progress=mp.occurred,
            progress_reason=mp.reason,
            poll_pct=cur_pct if is_poll and status == "ok" else None,
            poll_done=cur_done if is_poll and status == "ok" else False,
        )

        events = list(state.events)[state.evidence_from_index:] + [event]
        signals = detector_signals(events, self.cfg)
        if (
            event.status == "error"
            and state.last_recovery_cause
            and (event.failure_group or event.failure_sig or event.error_class or "") == state.last_recovery_cause
        ):
            signals["post_recovery_recurrence"] = 1
        else:
            signals["post_recovery_recurrence"] = 0
        traj = self.registry.get_session(session_id)
        if signals["same_failure_after_mutation"] and event.failure_group in traj.recent_failure_groups:
            signals["session_trajectory_recurrence"] = 1
        else:
            signals["session_trajectory_recurrence"] = 0
        steps_since = (
            0 if event.material_progress else state.steps_since_material_progress + 1
        )
        delta = score_delta(
            signals, self.cfg, event=event,
            steps_since_material_progress=steps_since,
        )
        state.stall_score = max(0, state.stall_score + delta)
        state.push(event)
        state.last_result_fingerprint = rfp
        if event.material_progress:
            state.steps_since_material_progress = 0
            state.material_progress_events += 1
            state.last_material_progress_index = len(state.events)
            state.last_material_desc = (
                event.progress_reason or f"{family.lower()} material"
            )
        else:
            state.steps_since_material_progress = steps_since
        if is_poll and status == "ok":
            state.last_poll_pct = cur_pct
            state.last_poll_done = cur_done

        decision = decide(state.stall_score, self.cfg)
        if decision == "RECOVER":
            state.recovery_count += 1
            if state.recovery_count > self.cfg.recovery.max_attempts:
                state.hard_stop = True  # budget exhausted -> escalate to hard stop
                decision = "BLOCK"
            else:
                state.pending_recovery_cause = event.failure_group or event.failure_sig or event.error_class or ""
                if state.pending_recovery_cause and state.pending_recovery_cause == state.last_recovery_cause:
                    self.metrics.inc("suppressed_duplicate_recoveries")
                    state.stall_score = 0
                else:
                    state.pending_recovery = tool_call_id or f"{tool_name}:{len(state.events)}"
                # Fresh window + zeroed score after a delivered recovery: a
                # strategy change is rewarded, only persistence re-escalates.
                state.evidence_from_index = len(state.events)
                if state.pending_recovery:
                    state.stall_score = 0
        self._record(signals, decision, tool_name, session_id, turn_id, state,
                     material=event.material_progress)
        debug_line(
            self.cfg, tool_name, signals, state.stall_score, decision,
            session_id, turn_id,
            family=family,
            steps=state.steps_since_material_progress,
            material=event.material_progress,
        )

    # -- thinking loop detection -------------------------------------------

    def on_stream_delta(
        self,
        delta: str = "",
        kind: str = "",
        session_id: str = "",
        turn_id: str = "",
        iteration: Any = None,
        **_: Any,
    ) -> None:
        """Watch reasoning deltas for pure thinking loops (no tool calls).

        Detects verbatim repeated segments (A A A) and ABAB/ABCABC cycles over
        normalized reasoning blocks within one generation. Requires Hermes'
        global ``plugins.stream_reasoning_deltas: true`` opt-in; without it
        this hook never sees reasoning text and is inert.
        """
        rl = self.cfg.reasoning_loop
        if not self.cfg.enabled or not rl.enabled or not delta or kind != "reasoning":
            return
        state = self.registry.get(session_id, turn_id)
        it = str(iteration) if iteration is not None else None
        if it != state.last_iteration:
            # new generation -> fresh reasoning stream
            state.reasoning_segments.clear()
            state.reasoning_tail = ""
            state.reasoning_run = 0
            state.reasoning_flagged = False
            state.reasoning_deltas = 0
            state.reasoning_chars = 0
            state.last_iteration = it

        state.reasoning_tail += delta
        state.reasoning_deltas += 1
        state.reasoning_chars += len(delta)
        while "\n" in state.reasoning_tail:
            line, state.reasoning_tail = state.reasoning_tail.split("\n", 1)
            if line.strip():
                state.reasoning_segments.append(line)

        run = repeated_thinking(list(state.reasoning_segments), rl.threshold)
        period = reasoning_cycle(
            list(state.reasoning_segments),
            rl.cycle_repetitions,
            rl.max_cycle_length,
        )
        if self.cfg.debug and state.reasoning_deltas % 200 == 0:
            prev = ""
            if state.reasoning_segments:
                prev = str(state.reasoning_segments[-1]).strip()[-120:]
            logger.warning(
                "[progress-guard] reasoning deltas=%d chars=%d segments=%d "
                "run=%d period=%d last='%s'",
                state.reasoning_deltas, state.reasoning_chars,
                len(state.reasoning_segments), run, period, prev,
            )
        if not state.reasoning_flagged and (run >= rl.threshold or period >= 2):
            state.reasoning_flagged = True
            state.reasoning_run = run
            # A thinking loop is one RECOVER-level event, not a compounding
            # score: flag it, inject guidance once, and let the recovery
            # budget escalate to a hard stop if it keeps recurring.
            if period >= 2:
                self.metrics.inc("reasoning_cycles")
            else:
                self.metrics.inc("thinking_loops")
            state.stall_score = max(state.stall_score, self.cfg.policy.recover_score)
            decision = decide(state.stall_score, self.cfg)
            if decision == "RECOVER":
                state.recovery_count += 1
                if state.recovery_count > self.cfg.recovery.max_attempts:
                    state.hard_stop = True
                    decision = "BLOCK"
                else:
                    state.pending_thinking_recovery = True
            debug_line(
                self.cfg, "reasoning",
                {"thinking_repeat": run, "reasoning_period": period},
                state.stall_score, decision, session_id, turn_id,
            )

    # -- recovery injection ------------------------------------------------

    def on_transform_tool_result(
        self,
        tool_name: str = "",
        result: Any = None,
        session_id: str = "",
        turn_id: str = "",
        tool_call_id: str = "",
        **_: Any,
    ) -> Optional[str]:
        if not self.cfg.enabled:
            return None
        state = self.registry.get(session_id, turn_id)
        if state.pending_thinking_recovery:
            state.pending_thinking_recovery = False
            msg = thinking_recovery_message()
            return (result + "\n\n" + msg) if isinstance(result, str) else msg
        if not state.pending_recovery:
            return None
        if tool_call_id and tool_call_id != state.pending_recovery:
            return None  # different call — don't mislabel another result
        state.pending_recovery = None
        if state.pending_recovery_cause:
            state.last_recovery_cause = state.pending_recovery_cause
            state.pending_recovery_cause = ""
        msg = recovery_message(
            list(state.events),
            family_trail_value=family_trail(list(state.events)),
            steps=state.steps_since_material_progress,
            last_progress=state.last_material_desc,
        )
        return (result + "\n\n" + msg) if isinstance(result, str) else msg

    # -- blocking ----------------------------------------------------------

    def on_pre_tool_call(
        self,
        tool_name: str = "",
        session_id: str = "",
        turn_id: str = "",
        **_: Any,
    ) -> Optional[Dict[str, str]]:
        if not self.cfg.enabled:
            return None
        state = self.registry.get(session_id, turn_id)
        if state.hard_stop or state.stall_score >= self.cfg.policy.block_score:
            if state.blocked_once:
                # the model keeps trying to work after the hard stop fired
                self.metrics.inc("blocked_calls_after_hard_stop")
            else:
                self.metrics.inc("hard_stops")
            state.blocked_once = True
            debug_line(
                self.cfg, tool_name, {}, state.stall_score, "HARD_STOP",
                session_id, turn_id,
            )
            return {"action": "block", "message": hard_stop_message()}
        return None

    # -- lifecycle ---------------------------------------------------------

    def on_session_start(
        self, session_id: str = "", model: str = "", platform: str = "", **_: Any
    ) -> None:
        # A brand-new session begins a clean trajectory.
        self.registry.reset_session(session_id)

    def on_session_end(
        self, task_id: str = "", session_id: str = "", turn_id: str = "", **_: Any
    ) -> None:
        sid = session_id or task_id
        if not sid:
            return
        state = self.registry._turns.get((sid, turn_id or ""))
        if state is not None and len(state.events):
            traj = self.registry.get_session(sid)
            fams = [e.family for e in state.events if e.family]
            for f in fams[-8:]:
                traj.recent_families.append(f)
            for e in state.events:
                if e.status == "error" and (e.failure_sig or e.error_class):
                    traj.recent_failure_signatures.append(e.failure_sig or e.error_class or "")
                    if e.failure_group:
                        traj.recent_failure_groups.append(e.failure_group)
            if state.material_progress_events:
                traj.last_material_progress = state.last_material_desc or "material event"
            traj.carryovers += 1
            self.metrics.inc("session_trajectory_carryovers")
        self.registry.drop(sid, turn_id or "")

    def on_session_finalize(
        self, session_id: str = "", platform: str = "", **_: Any
    ) -> None:
        # Real session teardown -> drop session trajectory + turns.
        if session_id:
            self.registry.drop_session(session_id)
        if self.cfg.debug:
            snapshot = dict(sorted(self.metrics.snapshot().items()))
            summary = " ".join(f"{k}={v}" for k, v in snapshot.items())
            logger.warning(
                "[progress-guard] SESSION SUMMARY session=%s platform=%s %s",
                session_id or "", platform or "", summary,
            )

    def on_session_reset(self, **_: Any) -> None:
        self.registry.clear()

    # -- metrics -----------------------------------------------------------

    def _record(self, signals, decision, tool_name, session_id, turn_id, state,
                material=False) -> None:
        m = self.metrics
        er = self.cfg.exact_repeat
        ir = self.cfg.identical_result
        rf = self.cfg.repeated_failure
        if er.enabled and signals["exact_repeat"] >= er.threshold:
            m.inc("exact_repeats")
        if ir.enabled and signals["identical_result"] >= ir.threshold:
            m.inc("repeated_results")
        if rf.enabled and signals["repeated_failure"] >= rf.threshold:
            m.inc("repeated_failures")
        if signals.get("same_failure_after_mutation", 0):
            m.inc("same_failure_after_mutation")
        if signals.get("failure_improvement"):
            m.inc("failure_improvements")
        if signals.get("post_recovery_recurrence", 0):
            m.inc("post_recovery_recurrences")
        if signals.get("session_trajectory_recurrence", 0):
            m.inc("canonical_failure_matches")
        if self.cfg.cycle.enabled and signals["cycle"]:
            m.inc("cycles_detected")
        if self.cfg.family_cycle.enabled and signals["family_cycle"] >= 2:
            m.inc("action_family_cycles")
        if self.cfg.canonical.enabled and signals["canonical_matches"] >= 2:
            m.inc("canonical_action_matches")
        if material:
            m.inc("material_progress_events")
        if decision == "RECOVER":
            m.inc("recoveries_triggered")
        if decision == "BLOCK":
            m.inc("blocks")
