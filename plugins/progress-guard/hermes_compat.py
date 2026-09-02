"""Adapter over Hermes-internal classifiers (handoff §6).

Hermes is the source of truth for tool classification; importing its helpers
directly couples the plugin to internal module layout. This module centralizes
those imports behind one point so a minor upstream move cannot break the
plugin, and keeps a conservative local fallback for standalone/test runs.
"""

from __future__ import annotations

import json
from typing import Any

try:  # Hermes present -> authoritative classification
    from agent.tool_guardrails import (  # type: ignore
        IDEMPOTENT_TOOL_NAMES,
        MUTATING_TOOL_NAMES,
        is_stall_guard_repeatable,
    )
    from agent.tool_result_classification import (  # type: ignore
        file_mutation_result_landed,
        tool_may_have_side_effect,
    )
except Exception:  # standalone / test environment
    # ponytail: minimal fallback; names mirror current Hermes membership so the
    # two don't drift when the plugin runs outside Hermes.
    MUTATING_TOOL_NAMES = frozenset(
        {
            "terminal", "execute_code", "write_file", "patch", "todo_list",
            "memory", "skill_manage", "browser_click", "browser_type",
            "browser_press", "browser_scroll", "browser_navigate",
            "send_message", "cronjob_manage", "delegate_task", "process_manage",
        }
    )
    IDEMPOTENT_TOOL_NAMES = frozenset(
        {
            "read_file", "search_files", "web_search", "web_extract",
            "session_search", "browser_snapshot", "browser_console",
            "mcp_filesystem_read_file",
        }
    )
    _STALL_GUARD_REPEATABLE_TOOLS = frozenset({"process_manage"})
    _POLL_SUFFIXES = ("_get_result", "_poll")

    def is_stall_guard_repeatable(tool_name: str) -> bool:
        if tool_name in _STALL_GUARD_REPEATABLE_TOOLS:
            return True
        return tool_name.endswith(_POLL_SUFFIXES)

    def tool_may_have_side_effect(tool_name: str) -> bool:
        # mirrors agent/tool_result_classification.NO_EFFECT_TOOL_NAMES
        no_effect = frozenset(
            {
                "read_file", "search_files", "session_search", "skill_view",
                "skills_list", "web_extract", "web_search", "vision_analyze",
                "browser_snapshot", "browser_get_images", "browser_console",
                "read_terminal",
            }
        )
        return tool_name not in no_effect

    def file_mutation_result_landed(tool_name: str, result: Any) -> bool:
        """Conservative replica of Hermes' landed detector.

        True only for write_file/patch whose result is JSON dict without a
        top-level error; write_file additionally needs 'bytes_written',
        patch needs 'success' is True.
        """
        if tool_name not in {"write_file", "patch"} or not isinstance(result, str):
            return False
        try:
            data = json.loads(result.strip())
        except Exception:
            return False
        if not isinstance(data, dict) or data.get("error"):
            return False
        if tool_name == "write_file":
            return "bytes_written" in data
        if tool_name == "patch":
            return data.get("success") is True
        return False
