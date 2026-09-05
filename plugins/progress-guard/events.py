"""Internal event model for a single tool execution.

Deliberately stores only fingerprints + metadata (handoff §6): never the full
result payloads. A small sanitized preview may be attached for diagnostics in
future versions; not in the MVP.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolEvent:
    tool_name: str
    args_fingerprint: str
    result_fingerprint: str
    status: str
    error_class: Optional[str]
    is_mutation: bool
    is_poll: bool
    tool_call_id: str = ""
    timestamp: float = 0.0
    failure_sig: Optional[str] = None
    failure_group: Optional[str] = None
    failure_count: Optional[int] = None
    # Phase 1.6: action family / canonical key / material-progress verdict.
    family: str = ""
    canonical_action: str = ""
    mutation_landed: bool = False
    material_progress: bool = False
    progress_reason: str = ""
    poll_pct: Optional[int] = None
    poll_done: bool = False
