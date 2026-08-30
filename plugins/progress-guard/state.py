"""In-memory state, isolated per ``(session_id, turn_id)`` (handoff §7).

Only fingerprints and small counters are kept. Everything is dropped on
session end/reset — nothing persists, nothing crosses turns.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Dict, Optional, Tuple

from .events import ToolEvent


class TurnState:
    def __init__(self) -> None:
        self.events: deque = deque(maxlen=64)
        self.stall_score: int = 0
        self.recovery_count: int = 0
        self.hard_stop: bool = False
        self.last_result_fingerprint: Optional[str] = None
        self.pending_recovery: Optional[str] = None
        self.created_at: float = time.monotonic()

    def push(self, event: ToolEvent) -> None:
        self.events.append(event)


class StateRegistry:
    """Per-(session, turn) TurnState plus explicit cleanup."""

    def __init__(self) -> None:
        self._turns: Dict[Tuple[str, str], TurnState] = {}

    def get(self, session_id: str, turn_id: str) -> TurnState:
        key = (session_id or "", turn_id or "")
        state = self._turns.get(key)
        if state is None:
            state = TurnState()
            self._turns[key] = state
        return state

    def drop_session(self, session_id: str) -> None:
        sid = session_id or ""
        for key in [k for k in self._turns if k[0] == sid]:
            del self._turns[key]

    def drop(self, session_id: str, turn_id: str) -> None:
        self._turns.pop((session_id or "", turn_id or ""), None)

    def clear(self) -> None:
        self._turns.clear()

    def size(self) -> int:
        return len(self._turns)
