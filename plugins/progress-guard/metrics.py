"""Observability counters (handoff §17). Hashes and metadata only, never
argument/result contents, by default.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict


class Metrics:
    def __init__(self) -> None:
        self._counters: Dict[str, int] = defaultdict(int)

    def inc(self, key: str, n: int = 1) -> None:
        self._counters[key] += n

    def snapshot(self) -> Dict[str, int]:
        return dict(self._counters)
