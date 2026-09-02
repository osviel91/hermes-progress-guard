"""Debug line (handoff §23): explains every decision when debug is enabled.

Example:
    [progress-guard] tool=search_files session=s1 turn=t1 family=SEARCH
    family_cycle=2 exact_repeat=0 result_repeat=0 cycle=false failure_repeat=0
    steps_since=6 material=false stall_score=7 decision=RECOVER
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

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
    *,
    family: str = "",
    steps: Optional[int] = None,
    material: bool = False,
) -> None:
    if not cfg.debug:
        return
    logger.warning(
        "[progress-guard] tool=%s session=%s turn=%s family=%s "
        "family_cycle=%s exact_repeat=%s result_repeat=%s cycle=%s "
        "failure_repeat=%s thinking_repeat=%s canonical_matches=%s "
        "steps_since=%s material=%s stall_score=%d decision=%s",
        tool_name,
        session_id or "-",
        turn_id or "-",
        family or "-",
        signals.get("family_cycle", 0),
        signals.get("exact_repeat", 0),
        signals.get("identical_result", 0),
        _flag(signals.get("cycle", False)),
        signals.get("repeated_failure", 0),
        signals.get("thinking_repeat", 0),
        signals.get("canonical_matches", 0),
        steps if steps is not None else "-",
        _flag(material),
        score,
        decision,
    )
