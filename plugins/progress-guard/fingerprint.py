"""Deterministic fingerprints (handoff §9).

Action fingerprint = sha256 of canonical JSON of ``{tool_name, normalized_args}``.
Result fingerprint = sha256 of the normalized result string.
Both are deterministic and cheap; nothing else is stored.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def action_fingerprint(tool_name: str, normalized_args: Any) -> str:
    return fingerprint({"t": tool_name, "a": normalized_args})


def result_fingerprint(normalized_result: str) -> str:
    return fingerprint(normalized_result)
