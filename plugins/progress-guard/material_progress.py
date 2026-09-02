"""Material progress (handoff §4, §5, §11).

Separates "the tool call succeeded" from "the trajectory materially advanced".
A successful mutating call is NOT material progress by itself: only evidence
that an observable state change actually happened counts. Everything else
(novel output, changed query, fresh read, another search, a landed-lookalike
bookkeeping mutation) is deliberately *not* material — it feeds novelty, not
progress.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_COMPLETED = re.compile(
    r"\b(done|completed|complete|succeeded|success|finished|passed|"
    r"all tests? passed)\b"
)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


@dataclass(frozen=True)
class MaterialProgress:
    occurred: bool = False
    confidence: str = ""  # high | medium | low
    reason: str = ""
    source: str = ""  # which rule fired


def _percent_of(result: str) -> Optional[int]:
    m = _PERCENT.search(result or "")
    if not m:
        return None
    try:
        return int(float(m.group(1)))
    except (TypeError, ValueError):
        return None


def _completed_now(result: str) -> bool:
    return bool(_COMPLETED.search((result or "").lower()))


def poll_state(result: str):
    """Public poll-derived state: (percent, completed) for a result string."""
    return _percent_of(result), _completed_now(result)


def assess(
    *,
    tool_name: str,
    result: str,
    status: str,
    family: str,
    is_mutation: bool,
    prev_poll_pct: Optional[int],
    prev_poll_done: bool,
    mutation_landed: Optional[bool] = None,
) -> MaterialProgress:
    """Deterministic material-progress verdict for one tool outcome."""
    if status != "ok":
        return MaterialProgress(reason="non-ok result")

    if family == "POLL":
        if prev_poll_done:
            return MaterialProgress(reason="poll already complete")
        if _completed_now(result):
            return MaterialProgress(
                True, "high", "poll reached completion", "poll"
            )
        cur = _percent_of(result)
        if cur is not None:
            if prev_poll_pct is not None and cur > prev_poll_pct:
                return MaterialProgress(
                    True, "medium", f"poll progressed {prev_poll_pct}% -> {cur}%", "poll"
                )
            if prev_poll_pct is not None and cur == prev_poll_pct:
                return MaterialProgress(reason="poll unchanged")
        return MaterialProgress(reason="poll intermediate")

    if is_mutation and mutation_landed:
        # Hermes confirmed the mutation really landed (bytes_written /
        # success:true) -> strong material evidence.
        return MaterialProgress(True, "high", "file mutation landed", "file_mutation")

    # A successful run alone is not material evidence (handoff §5): bookkeeping
    # mutations, fresh searches, changed reads, extra reasoning -> novelty, not
    # progress. Verification-driven progress rules are deferred (see analysis
    # doc §5) in favor of conservative false negatives.
    return MaterialProgress(reason="no material evidence")
