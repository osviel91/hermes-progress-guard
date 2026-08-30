"""Recovery messaging (handoff §12, §14).

The RECOVER message interrupts the stalled strategy and forces a replan. The
HARD STOP message, shown when the recovery budget is exhausted, ends tool
execution and asks for an honest final report. No internal implementation
details leak into either message.
"""

from __future__ import annotations

from typing import List

from .events import ToolEvent

_RECOVERY_TEMPLATE = (
    "PROGRESS GUARD: CURRENT STRATEGY STALLED\n"
    "The recent sequence of tool calls is not producing meaningful progress.\n"
    "Detected pattern:\n{evidence}\n"
    "Repeated actions/results have not materially changed the available information.\n"
    "Do not repeat the blocked action or a trivial variation of the same strategy.\n"
    "Before calling another tool:\n"
    "1. Re-evaluate the original objective.\n"
    "2. Summarize what is already known.\n"
    "3. Identify the unresolved blocker.\n"
    "4. Choose a materially different strategy.\n"
    "5. If no viable alternative exists, report the blocker instead of retrying."
)

_HARD_STOP_TEMPLATE = (
    "PROGRESS GUARD: STRATEGY EXHAUSTED — HARD STOP\n"
    "The recent strategy has stalled repeatedly and the recovery budget is exhausted.\n"
    "Stop executing tools and produce the final response covering:\n"
    "- objective\n"
    "- progress achieved\n"
    "- blocker\n"
    "- strategies attempted\n"
    "- reason for stopping\n"
    "Do not invent success."
)

_THINKING_TEMPLATE = (
    "PROGRESS GUARD: THINKING LOOP DETECTED\n"
    "The reasoning stream has been repeating the same content verbatim "
    "without converging and without calling any tool.\n"
    "Stop re-reasoning about the same point. Decide what single concrete "
    "action is missing and take it, or report the blocker.\n"
    "Do not repeat the same thought again."
)


def evidence_list(events: List[ToolEvent], limit: int = 6) -> str:
    """Sanitized preview: tool names only, no args/result contents."""
    return "\n".join(f"- {e.tool_name}(...)" for e in events[-limit:])


def recovery_message(events: List[ToolEvent], limit: int = 6) -> str:
    return _RECOVERY_TEMPLATE.format(evidence=evidence_list(events, limit))


def hard_stop_message() -> str:
    return _HARD_STOP_TEMPLATE


def thinking_recovery_message() -> str:
    return _THINKING_TEMPLATE
