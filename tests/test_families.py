"""Action-family classification + canonical keys (handoff §7-§9)."""

from hermes_plugins.progress_guard import canonical, families

POLL = families.classify_action
CANON = canonical.canonical_action


def test_poll_suffix_wins_regardless_of_name():
    assert POLL("job_poll") == "POLL"
    assert POLL("process_get_result") == "POLL"
    assert POLL("job_poll", {"job_id": 1}) == "POLL"


def test_read_family():
    assert POLL("read_file") == "READ"
    assert POLL("read_terminal") == "READ"
    assert POLL("mcp_filesystem_read_file") == "READ"
    assert POLL("skill_view") == "READ"


def test_search_family():
    assert POLL("search_files") == "SEARCH"
    assert POLL("grep") == "SEARCH"
    assert POLL("glob") == "SEARCH"
    assert POLL("web_search") == "SEARCH"
    assert POLL("session_search") == "SEARCH"


def test_mutate_execute_delegate_communicate_memory():
    assert POLL("write_file") == "MUTATE"
    assert POLL("patch") == "MUTATE"
    assert POLL("browser_click") == "MUTATE"
    assert POLL("terminal") == "EXECUTE"
    assert POLL("execute_code") == "EXECUTE"
    assert POLL("delegate_task") == "DELEGATE"
    assert POLL("send_message") == "COMMUNICATE"
    assert POLL("memory") == "MEMORY"


def test_unknown_falls_back_to_other():
    assert POLL("") == "OTHER"
    assert POLL("mystery_tool") == "OTHER"


def test_search_canonical_ignores_word_order():
    a = CANON("search_files", "SEARCH", {"query": "Hermes tool loops"})
    b = CANON("search_files", "SEARCH", {"query": "tool loops Hermes"})
    assert a == b
    assert a.startswith("search|")


def test_search_canonical_ignores_case_and_punctuation():
    a = CANON("search_files", "SEARCH", {"query": "Verify tests, NOW!"})
    b = CANON("search_files", "SEARCH", {"query": "verify tests now"})
    assert a == b


def test_search_canonical_distinguishes_different_terms():
    a = CANON("search_files", "SEARCH", {"query": "alpha beta"})
    b = CANON("search_files", "SEARCH", {"query": "alpha gamma"})
    assert a != b


def test_execute_canonical_collapses_flag_ordering():
    a = CANON("terminal", "EXECUTE", {"command": "grep -n -i foo"})
    b = CANON("terminal", "EXECUTE", {"command": "grep -i -n foo"})
    assert a == b
    assert a.startswith("exec|grep|")


def test_read_canonical_uses_target_path():
    a = CANON("read_file", "READ", {"path": "/a/b.py"})
    b = CANON("read_file", "READ", {"path": "/a/b.py"})
    c = CANON("read_file", "READ", {"path": "/x/y.py"})
    assert a == b
    assert a != c
    assert a.startswith("read|/a/b.py|")


def test_poll_canonical_is_empty():
    assert CANON("job_poll", "POLL", {"job_id": 3}) == ""


def test_mutate_canonical_uses_file_target():
    a = CANON("write_file", "MUTATE", {"path": "/tmp/f.py", "content": "x"})
    b = CANON("write_file", "MUTATE", {"path": "/tmp/f.py", "content": "y"})
    assert a == b  # content change is jitter for the canonical key
    assert a == "write|/tmp/f.py"
