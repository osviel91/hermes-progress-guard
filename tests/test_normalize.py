"""Unit tests: normalization (handoff §8)."""

from hermes_plugins.progress_guard import normalize


def test_drop_fields_removes_volatile_keys_recursively():
    args = {"query": "x", "timestamp": "2026-08-30T18:30:01", "nested": {"trace_id": "abc", "keep": 1}}
    out = normalize.drop_fields(args, {"timestamp", "trace_id"})
    assert out == {"query": "x", "nested": {"keep": 1}}


def test_ignored_fields_only_removes_whats_configured():
    args = {"query": "x", "timestamp": "t", "important": "y"}
    out = normalize.normalize_args(args, ("timestamp",))
    assert "timestamp" not in out
    assert out["important"] == "y"
    assert out["query"] == "x"


def test_ignored_fields_never_drop_unknown_semantics():
    args = {"query": "x", "timestamp": "t"}
    out = normalize.normalize_args(args, ())  # nothing ignored
    assert out == args


def test_normalize_result_deterministic_for_dict_and_string():
    d = {"b": 2, "a": [1, 2]}
    assert normalize.normalize_result(d) == normalize.normalize_result({"a": [1, 2], "b": 2})
    assert isinstance(normalize.normalize_result(d), str)
    assert normalize.normalize_result("plain") == "plain"


def test_error_class_stable_across_noise():
    c1 = normalize.error_class("PatchError", "no matching context found at line 42 in /tmp/abc.py")
    c2 = normalize.error_class("PatchError", "no matching context found at line 999 in /tmp/xyz.py")
    assert c1 == c2
    c3 = normalize.error_class("PatchError", "no matching context found at line 7 in /data/file.py")
    assert c1 == c3


def test_error_class_distinguishes_different_failures():
    a = normalize.error_class("PatchError", "context mismatch")
    b = normalize.error_class("PermissionError", "context mismatch")
    assert a != b
    c = normalize.error_class("PatchError", "context mismatch at line 3")
    d = normalize.error_class("PatchError", "something entirely different")
    assert c != d


def test_error_class_empty_when_no_error():
    assert normalize.error_class(None, None) == ""
    assert normalize.error_class("", "") == ""


def test_failure_group_collapses_failure_counts():
    a = normalize.failure_signature("tool_error", "2 tests failed", "")
    b = normalize.failure_signature("tool_error", "1 test failed", "")
    assert a != b
    assert normalize.failure_group(a) == normalize.failure_group(b)


def test_failure_count_reads_explicit_counts_only():
    assert normalize.failure_count("2 tests failed") == 2
    assert normalize.failure_count("1 error") == 1
    assert normalize.failure_count("AssertionError at line 9") is None
