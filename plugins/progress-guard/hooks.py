"""Hook wiring for Progress Guard (handoff §13).

    post_tool_call          record event -> normalize -> fingerprint -> update
                            detectors -> compute score -> decide
    transform_tool_result   inject recovery/replan guidance into the result
                            the model sees next (only fires for executed calls)
    pre_tool_call           block the next call when the score is past the
                            block threshold or the recovery budget is exhausted
    on_session_end/reset    drop per-(session, turn) state

Blocking uses Hermes' official ``{"action": "block", "message": ...}``
contract; the message reaches the model as a synthetic error result.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .config import ProgressGuardConfig
from .debug import debug_line
from .detectors import evaluate as detector_signals
from .events import ToolEvent
from .fingerprint import action_fingerprint, result_fingerprint
from .metrics import Metrics
from .normalize import error_class, normalize_args, normalize_result
from .policy import decide, score_delta
from .recovery import hard_stop_message, recovery_message
from .state import StateRegistry

logger = logging.getLogger(__name__)

try:  # authoritative classification lives in Hermes core
    from agent.tool_guardrails import (  # type: ignore
        IDEMPOTENT_TOOL_NAMES,
        MUTATING_TOOL_NAMES,
        is_stall_guard_repeatable,
    )
except Exception:  # standalone / test environment
    # ponytail: minimal fallback so the plugin also works outside Hermes;
    # Hermes' own lists are authoritative when it runs under Hermes.
    IDEMPOTENT_TOOL_NAMES = frozenset(
        {
            "read_file", "search_files", "web_search", "web_extract",
            "session_search", "browser_snapshot", "browser_console",
            "mcp_filesystem_read_file",
        }
    )
    MUTATING_TOOL_NAMES = frozenset(
        {
            "terminal", "execute_code", "write_file", "patch", "todo",
            "memory", "skill_manage", "browser_click", "browser_type",
            "browser_press", "browser_scroll", "browser_navigate",
            "send_message", "cronjob", "delegate_task", "process",
        }
    )
    _STALL_GUARD_REPEATABLE_TOOLS = frozenset({"process"})
    _POLL_SUFFIXES = ("_get_result", "_poll")

    def is_stall_guard_repeatable(tool_name: str) -> bool:
        if tool_name in _STALL_GUARD_REPEATABLE_TOOLS:
            return True
        return tool_name.endswith(_POLL_SUFFIXES)


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
        ctx.register_hook("on_session_end", self.on_session_end)
        ctx.register_hook("on_session_reset", self.on_session_reset)

    # -- classification ----------------------------------------------------

    def _classify(self, tool_name: str) -> tuple:
        return (
            tool_name in MUTATING_TOOL_NAMES,
            is_stall_guard_repeatable(tool_name),
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
        if status in (None, "blocked", "cancelled"):
            return  # artifact of our own block / user cancellation — not agent behavior

        try:
            afp = action_fingerprint(tool_name, normalize_args(args, self.cfg.ignored_fields))
            rfp = result_fingerprint(normalize_result(result))
        except Exception:
            return  # never let a fingerprinting bug take down the agent

        is_mutation, is_poll = self._classify(tool_name)
        event = ToolEvent(
            tool_name=tool_name,
            args_fingerprint=afp,
            result_fingerprint=rfp,
            status=status or "ok",
            error_class=error_class(error_type, error_message) if status == "error" else None,
            is_mutation=is_mutation,
            is_poll=is_poll,
            tool_call_id=tool_call_id or "",
        )

        events = list(state.events) + [event]
        signals = detector_signals(events, self.cfg)
        delta = score_delta(
            signals, self.cfg, event=event,
            prev_result_fingerprint=state.last_result_fingerprint,
        )
        state.stall_score = max(0, state.stall_score + delta)
        state.push(event)
        state.last_result_fingerprint = rfp

        decision = decide(state.stall_score, self.cfg)
        if decision == "RECOVER":
            state.recovery_count += 1
            if state.recovery_count > self.cfg.recovery.max_attempts:
                state.hard_stop = True  # budget exhausted -> escalate to hard stop
                decision = "BLOCK"
            else:
                state.pending_recovery = tool_call_id or f"{tool_name}:{len(state.events)}"
        self._record(signals, decision, tool_name, session_id, turn_id, state)
        debug_line(
            self.cfg, tool_name, signals, state.stall_score, decision,
            session_id, turn_id,
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
        if not self.cfg.enabled or not isinstance(result, str):
            return None
        state = self.registry.get(session_id, turn_id)
        if not state.pending_recovery:
            return None
        if tool_call_id and tool_call_id != state.pending_recovery:
            return None  # different call — don't mislabel another result
        state.pending_recovery = None
        return result + "\n\n" + recovery_message(list(state.events))

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
            self.metrics.inc("hard_stops")
            debug_line(
                self.cfg, tool_name, {}, state.stall_score, "HARD_STOP",
                session_id, turn_id,
            )
            return {"action": "block", "message": hard_stop_message()}
        return None

    # -- lifecycle ---------------------------------------------------------

    def on_session_end(
        self, task_id: str = "", session_id: str = "", turn_id: str = "", **_: Any
    ) -> None:
        self.registry.drop_session(session_id or task_id)

    def on_session_reset(self, **_: Any) -> None:
        self.registry.clear()

    # -- metrics -----------------------------------------------------------

    def _record(self, signals, decision, tool_name, session_id, turn_id, state) -> None:
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
        if self.cfg.cycle.enabled and signals["cycle"]:
            m.inc("cycles_detected")
        if decision == "RECOVER":
            m.inc("recoveries_triggered")
        if decision == "BLOCK":
            m.inc("blocks")
