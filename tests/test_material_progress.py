"""Material-progress verdicts + the landed-mutation adapter (handoff §4-§5)."""

from hermes_plugins.progress_guard import material_progress as mp
from hermes_plugins.progress_guard import hermes_compat as compat


def _assess(tool, result, status="ok", family="MUTATE", is_mutation=False,
            prev_pct=None, prev_done=False, landed=None):
    return mp.assess(
        tool_name=tool, result=result, status=status, family=family,
        is_mutation=is_mutation, prev_poll_pct=prev_pct,
        prev_poll_done=prev_done, mutation_landed=landed,
    )


# -- landed mutation adapter ------------------------------------------------

def test_landed_write_file_needs_bytes_written():
    assert compat.file_mutation_result_landed("write_file", '{"bytes_written": 42}') is True
    assert compat.file_mutation_result_landed("write_file", '{"ok": true}') is False


def test_landed_patch_needs_success_true():
    assert compat.file_mutation_result_landed("patch", '{"success": true}') is True
    assert compat.file_mutation_result_landed("patch", '{"success": false}') is False
    assert compat.file_mutation_result_landed("patch", '{"applied": true}') is False


def test_landed_top_level_error_rejects_nested_allows():
    assert compat.file_mutation_result_landed("write_file", '{"error": "disk full"}') is False
    assert compat.file_mutation_result_landed(
        "write_file", '{"bytes_written": 5, "lint": {"error": "unused"}}'
    ) is True  # mirrors Hermes: only top-level error disqualifies


def test_landed_rejects_non_file_tools_and_non_json():
    assert compat.file_mutation_result_landed("terminal", '{"exit_code": 0}') is False
    assert compat.file_mutation_result_landed("write_file", "written") is False
    assert compat.file_mutation_result_landed("write_file", None) is False


# -- assess: mutation is only material when it provably landed --------------

def test_successful_mutation_without_landed_is_not_material():
    p = _assess("patch", "applied", is_mutation=True)
    assert p.occurred is False
    assert p.reason == "no material evidence"


def test_landed_mutation_is_material():
    p = _assess("patch", '{"success": true}', is_mutation=True, landed=True)
    assert p.occurred is True
    assert p.confidence == "high"
    assert p.source == "file_mutation"


def test_non_ok_result_is_never_material():
    p = _assess("patch", '{"success": true}', status="error", is_mutation=True, landed=True)
    assert p.occurred is False


def test_terminal_ok_alone_is_not_material():
    p = _assess("terminal", '{"exit_code": 0}', family="EXECUTE")
    assert p.occurred is False


# -- assess: polls ----------------------------------------------------------

def test_poll_first_observation_is_not_material():
    p = _assess("job_poll", "10%", family="POLL")
    assert p.occurred is False  # no prior state to compare


def test_poll_advance_is_material():
    p = _assess("job_poll", "40%", family="POLL", prev_pct=10)
    assert p.occurred is True
    assert p.confidence == "medium"


def test_poll_unchanged_is_not_material():
    p = _assess("job_poll", "40%", family="POLL", prev_pct=40)
    assert p.occurred is False
    assert p.reason == "poll unchanged"


def test_poll_completion_is_material():
    p = _assess("job_poll", "completed", family="POLL")
    assert p.occurred is True
    assert p.confidence == "high"


def test_poll_after_done_is_not_material_again():
    p = _assess("job_poll", "completed", family="POLL", prev_done=True)
    assert p.occurred is False


def test_poll_intermediate_without_percent_is_not_material():
    p = _assess("job_poll", "running", family="POLL")
    assert p.occurred is False
