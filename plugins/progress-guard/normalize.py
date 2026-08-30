"""Normalization before fingerprinting.

Two distinct jobs:

1. Args: drop volatile fields (timestamps, request ids, ...) so semantically
   identical calls fingerprint identically. The ignored set is explicit and
   configurable — fields are never stripped globally without knowing their
   meaning (handoff §8).
2. Results/errors: reduce to stable hashable forms so fingerprints are
   deterministic and error classes aren't hostage to line numbers or hex ids.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable


def drop_fields(value: Any, ignored: Iterable[str]) -> Any:
    """Recursively delete ``ignored`` keys from dicts, keep everything else."""
    ignored = frozenset(ignored)
    if isinstance(value, dict):
        return {
            k: drop_fields(v, ignored) for k, v in value.items() if k not in ignored
        }
    if isinstance(value, list):
        return [drop_fields(v, ignored) for v in value]
    return value


def normalize_args(args: Any, ignored_fields: Iterable[str]) -> Any:
    return drop_fields(args or {}, ignored_fields)


def normalize_result(result: Any) -> str:
    """Stable string form of a tool result for hashing."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(result)


# Noise that should never separate two otherwise-identical failures:
# line numbers, hex tokens, long digit runs, temp paths.
_ERR_NOISE = re.compile(
    r"\b0x[0-9a-fA-F]{4,}\b"            # hex ids/addresses
    r"|(?<![0-9a-f])[0-9a-f]{16,}(?![0-9a-f])"  # long opaque tokens
    r"|\b\d{4,}\b"                      # line numbers, sizes, codes
    r"|\bline\s+\d+\b"                  # "line 42" (any width)
    r"|\b\w+_\d+\b"                     # indexed names (node_12)
    r"|(?:^|(?<=\s))/[^\s'\"]*"         # absolute paths (/tmp/..., /data/...)
)


def error_class(error_type: Optional[str], error_message: Optional[str]) -> str:
    """Stable class string for a failure: type + de-noised message tail."""
    if not error_type and not error_message:
        return ""
    et = (error_type or "").strip().lower()
    em = _ERR_NOISE.sub("N", (error_message or "").strip().lower())
    return f"{et}|{em[:160]}"
