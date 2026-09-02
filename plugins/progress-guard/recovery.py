"""Recovery messaging (handoff §12, §17).

The RECOVER message interrupts the stalled strategy and forces a replan, and
carries, when known, the observed action-family trail, the number of steps
since the last material progress and the last material progress marker. The
HARD STOP message, shown when the recovery budget is exhausted, ends tool
execution and asks for an honest final report. No internal implementation
details leak into either message.
"""

from __future__ import annotations

from typing import List, Optional

from .events import ToolEvent

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


def family_trail(events: List[ToolEvent], limit: int = 8) -> Optional[str]:
    """Recent action-family sequence, e.g. READ → SEARCH → READ → SEARCH."""
    fams = [e.family for e in events[-limit:] if e.family]
    if not fams:
        return None
    seen = []
    for f in fams:
        if not seen or seen[-1] != f:
            seen.append(f)
    if len(seen) < 2:
        return None
    return " → ".join(seen)


def recovery_message(
    events: List[ToolEvent],
    limit: int = 6,
    *,
    family_trail_value: Optional[str] = None,
    steps: Optional[int] = None,
    last_progress: Optional[str] = None,
) -> str:
    context: List[str] = []
    if family_trail_value:
        context.append(f"Recent operational pattern: {family_trail_value}")
    if steps is not None:
        context.append(f"Actions since last material progress: {steps}")
    if last_progress:
        context.append(f"Last known material progress: {last_progress}")

    parts = [
        "PROGRESS GUARD: CURRENT STRATEGY STALLED",
        "The recent sequence of tool calls is not producing meaningful progress.",
        "Detected pattern:",
        evidence_list(events, limit),
    ]
    parts.extend(context)
    parts.extend(
        [
            "Repeated actions/results have not materially changed the available information.",
            "Do not repeat the blocked action or a trivial variation of the same strategy.",
            "Before calling another tool:",
            "1. Re-evaluate the original objective.",
            "2. Summarize what is already known.",
            "3. Identify the unresolved blocker.",
            "4. Choose a materially different strategy.",
            "5. If no viable alternative exists, report the blocker instead of retrying.",
        ]
    )
    return "\n".join(parts)


def hard_stop_message() -> str:
    return _HARD_STOP_TEMPLATE


def thinking_recovery_message() -> str:
    return _THINKING_TEMPLATE
