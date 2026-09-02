"""Canonical action fingerprints, semantic-lite (handoff §9).

A cheap, embedding-free canonical key per action that absorbs *semantic
jitter* — word order, casing, punctuation, volatile numbers — so near-identical
intents collide. Not a general semantic-equivalence solver: it exists only to
count canonical matches and to keep family-cycle evidence honest.

Only small derived strings are produced/stored; no raw payloads.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, List

_STOP = frozenset(
    {"a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "with", "at", "by"}
)
_NOISE = re.compile(r"\b0x[0-9a-fA-F]+\b|\b\d+\b|[^\w\s]")
_MAX_TOKENS = 6
_MAX_KEY = 64
_TARGET_KEYS = ("path", "file_path", "resource", "directory", "cwd", "filename", "name")
_COMMAND_KEYS = ("command", "cmd", "script")

_PUNCT_RE = re.compile(r"[^\w\s]")


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _iter_strings(v)


def _norm_word(word: str) -> str:
    word = unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode()
    return _PUNCT_RE.sub("", word).lower()


def _tokens_from(value: Any) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for s in _iter_strings(value):
        for word in _NOISE.sub(" ", s).split():
            w = _norm_word(word)
            if len(w) < 2 or w in _STOP or w in seen:
                continue
            seen.add(w)
            out.append(w)
        if len(out) >= _MAX_TOKENS:
            break
    return out


def _target_path(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    for k in _TARGET_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().rstrip("/")
    return ""


def canonical_action(tool_name: str, family: str, args: Any) -> str:
    """Stable semantic-lite key for an action; '' when nothing is usable."""
    tokens = _tokens_from(args) if isinstance(args, dict) else []
    fam = (family or "").lower()

    if fam == "read":
        path = _target_path(args)
        if path:
            return "read|%s|tokens=%d" % (path[:_MAX_KEY], len(tokens))
        return "read|tokens=%d" % len(tokens)

    if fam == "search":
        return "search|" + ",".join(sorted(tokens))[:_MAX_KEY]

    if fam == "execute":
        cmd = ""
        if isinstance(args, dict):
            for k in _COMMAND_KEYS:
                v = args.get(k)
                if isinstance(v, str) and v.strip():
                    cmd = v
                    break
        if cmd:
            parts = cmd.split()
            program = _norm_word(parts[0]) if parts else ""
            flags = sorted({p for p in parts[1:] if p.startswith("-")})[:4]
            return "exec|" + program + ("|" + ",".join(flags) if flags else "")
        return "exec|tokens=%d" % len(tokens)

    if fam == "mutate":
        path = _target_path(args)
        return ("write|" + path[:_MAX_KEY]) if path else "write"

    if fam in ("poll", "delegate", "communicate", "memory"):
        return ""  # identity already carries the intent

    return "other|tokens=%d" % len(tokens)
