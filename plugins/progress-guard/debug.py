"""Debug line (handoff §18): explains every decision when debug is enabled.

Example:
    [progress-guard] tool=web_search session=s1 turn=t1 exact_repeat=3
    result_repeat=0 cycle=false failure_repeat=0 stall_score=4 decision=WARN
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("progress-guard")

_TRUTHY = {"1", "true", "yes", "on"}


def _flag(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return str(value.strip().lower() in _TRUTHY)
    return str(value)


def debug_line(
    cfg: Any,
    tool_name: str,
    signals: Dict[str, Any],
    score: int,
    decision: str,
    session_id: str,
    turn_id: str,
) -> None:
    if not cfg.debug:
        return
    logger.warning(
        "[progress-guard] tool=%s session=%s turn=%s exact_repeat=%s "
        "result_repeat=%s cycle=%s failure_repeat=%s stall_score=%d decision=%s",
        tool_name,
        session_id or "-",
        turn_id or "-",
        signals.get("exact_repeat", 0),
        signals.get("identical_result", 0),
        _flag(signals.get("cycle", False)),
        signals.get("repeated_failure", 0),
        score,
        decision,
    )
