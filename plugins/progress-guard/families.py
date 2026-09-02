"""Action families (handoff §7-§8).

An abstract, coarse representation of a tool call's operational intent, so the
cycle detector can see READ/SEARCH oscillation even when the concrete tools
change (read_file -> grep -> search_files). Config-driven rules (name sets +
prefixes) rather than a giant if-chain; falls back to OTHER.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

# Family labels, in matching priority order (POLL must outrank EXECUTE/MUTATE
# because pollers often share mutating-looking names).
FAMILY_ORDER: Tuple[str, ...] = (
    "POLL", "READ", "SEARCH", "MUTATE", "EXECUTE",
    "DELEGATE", "COMMUNICATE", "MEMORY", "OTHER",
)

# (exact names, name prefixes). A tool matches a family if its name is in the
# exact set or starts with any prefix.
_FAMILY_RULES: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    # POLL: repeatable pollers — matched first, before their apparent family.
    "POLL": (
        ("process_manage",),
        (),
    ),
    "READ": (
        (
            "read_file", "read_terminal", "skill_view", "skills_list",
            "browser_snapshot", "browser_get_images", "browser_console",
            "mcp_filesystem_directory_tree",
        ),
        (
            "read_", "view_", "mcp_filesystem_read_", "mcp_filesystem_list_",
            "mcp_filesystem_get_file_info",
        ),
    ),
    "SEARCH": (
        ("search_files", "web_search", "session_search", "grep", "glob", "rg"),
        ("search_", "web_search_", "find_"),
    ),
    "MUTATE": (
        (
            "write_file", "patch", "browser_click", "browser_type",
            "browser_press", "browser_scroll", "browser_navigate",
            "todo", "todo_list", "cronjob", "cronjob_manage",
        ),
        ("write_", "edit_", "create_", "delete_", "remove_", "append_", "patch_"),
    ),
    "EXECUTE": (
        ("terminal", "execute_code", "bash", "shell"),
        ("run_", "exec_", "execute_"),
    ),
    "DELEGATE": (
        ("delegate_task", "delegate"),
        ("delegate_", "subagent_"),
    ),
    "COMMUNICATE": (
        ("send_message",),
        ("send_", "message_", "notify_"),
    ),
    "MEMORY": (
        ("memory", "skill_manage"),
        ("remember", "memorize"),
    ),
}


def classify_action(tool_name: str, args: Any = None) -> str:
    """Map a tool name (+args, reserved) to its action family label.

    Matches in :data:`FAMILY_ORDER` priority; a name ending in ``_poll`` /
    ``_get_result`` is always POLL regardless of its prefix rules.
    """
    name = tool_name or ""
    if name.endswith(("_poll", "_get_result")):
        return "POLL"
    for family in FAMILY_ORDER:
        if family == "OTHER":
            continue
        exacts, prefixes = _FAMILY_RULES[family]
        if name in exacts or any(name.startswith(p) for p in prefixes):
            return family
    return "OTHER"
