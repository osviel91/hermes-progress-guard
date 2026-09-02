"""In-memory state (handoff §7, §14, §15).

Per-``(session_id, turn_id)`` TurnState holds only fingerprints and small
counters. A lightweight rolling ``SessionTrajectory`` (small windows, no full
results, no persistence) survives internal continuations/compaction by living
at session granularity and is reset only on a real session boundary.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .events import ToolEvent


class TurnState:
    def __init__(self) -> None:
        self.events: deque = deque(maxlen=64)
        self.stall_score: int = 0
        self.recovery_count: int = 0
        self.hard_stop: bool = False
        self.blocked_once: bool = False
        self.last_result_fingerprint: Optional[str] = None
        self.pending_recovery: Optional[str] = None
        self.pending_thinking_recovery: bool = False
        self.created_at: float = time.monotonic()
        # Index (into events) from which detectors may consider evidence.
        # Advanced past the recovery-triggering event each time a RECOVER
        # injection is delivered so pre-recovery staleness cannot re-fire and
        # hard-block a workflow that actually changed strategy afterwards.
        self.evidence_from_index: int = 0
        # Phase 1.6: material progress / steps since / poll progression.
        self.steps_since_material_progress: int = 0
        self.last_material_progress_index: int = 0
        self.material_progress_events: int = 0
        self.last_material_desc: str = ""
        self.last_poll_pct: Optional[int] = None
        self.last_poll_done: bool = False
        # reasoning-loop tracking (on_stream_delta, kind="reasoning")
        self.reasoning_segments: deque = deque(maxlen=64)
        self.reasoning_tail: str = ""
        self.reasoning_run: int = 0
        self.reasoning_flagged: bool = False
        self.reasoning_deltas: int = 0
        self.reasoning_chars: int = 0
        self.last_iteration: Optional[str] = None

    def push(self, event: ToolEvent) -> None:
        self.events.append(event)


@dataclass
class SessionTrajectory:
    """Rolling, compact, per-session carryover across turns.

    Never stores full tool results. Only family windows, failure signatures and
    small counters survive from earlier turns so a mid-task continuation does
    not wipe all no-progress evidence.
    """

    recent_families: deque = field(default_factory=lambda: deque(maxlen=16))
    recent_failure_signatures: deque = field(default_factory=lambda: deque(maxlen=8))
    carryovers: int = 0
    last_material_progress: str = ""  # compact marker, e.g. "patch landed"


class StateRegistry:
    """Per-(session, turn) TurnState plus explicit cleanup."""

    def __init__(self) -> None:
        self._turns: Dict[Tuple[str, str], TurnState] = {}
        self._sessions: Dict[str, SessionTrajectory] = {}

    def get(self, session_id: str, turn_id: str) -> TurnState:
        key = (session_id or "", turn_id or "")
        state = self._turns.get(key)
        if state is None:
            state = TurnState()
            self._turns[key] = state
        return state

    def get_session(self, session_id: str) -> SessionTrajectory:
        sid = session_id or ""
        traj = self._sessions.get(sid)
        if traj is None:
            traj = SessionTrajectory()
            self._sessions[sid] = traj
        return traj

    def reset_session(self, session_id: str) -> None:
        """Brand-new session -> clean trajectory + drop its turns."""
        sid = session_id or ""
        self.drop_session(sid)
        self._sessions[sid] = SessionTrajectory()

    def drop_session(self, session_id: str) -> None:
        sid = session_id or ""
        for key in [k for k in self._turns if k[0] == sid]:
            del self._turns[key]
        self._sessions.pop(sid, None)

    def drop(self, session_id: str, turn_id: str) -> None:
        self._turns.pop((session_id or "", turn_id or ""), None)

    def clear(self) -> None:
        self._turns.clear()
        self._sessions.clear()

    def size(self) -> int:
        return len(self._turns)

    def session_count(self) -> int:
        return len(self._sessions)
