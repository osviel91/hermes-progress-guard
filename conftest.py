"""Test bootstrap: import the plugin the same way Hermes does.

Hermes loads hyphenated plugin dirs as ``hermes_plugins.<slug>`` via
``spec_from_file_location`` (hermes_cli/plugins.py ``_directory_module_name``).
We mirror that so tests exercise the real package, including relative imports.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_NS = "hermes_plugins"
_PLUGIN_DIR = Path(__file__).parent / "plugins" / "progress-guard"
_MODULE_NAME = f"{_NS}.progress_guard"


def _load_plugin():
    if _NS not in sys.modules:
        ns = types.ModuleType(_NS)
        ns.__path__ = []
        ns.__package__ = _NS
        sys.modules[_NS] = ns
    if _MODULE_NAME in sys.modules:
        del sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(
        _MODULE_NAME,
        _PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(_PLUGIN_DIR)],
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _MODULE_NAME
    mod.__path__ = [str(_PLUGIN_DIR)]
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


plugin = _load_plugin()


class FakeCtx:
    """Minimal stand-in for the Hermes PluginContext used in register()."""

    def __init__(self, settings=None):
        self.settings = settings or {}
        self.hooks = {}

    def get_config(self, key, default=None):
        cur = self.settings
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def register_hook(self, name, fn):
        self.hooks[name] = fn


@pytest.fixture
def make_guard():
    """Build a ProgressGuard wired onto a FakeCtx, returning the ctx too."""

    def _make(settings=None):
        ctx = FakeCtx(settings)
        from hermes_plugins.progress_guard.hooks import ProgressGuard

        guard = ProgressGuard(ctx)
        guard.install(ctx)
        return ctx, guard

    return _make


@pytest.fixture
def drive(make_guard):
    """Drive the plugin hooks like Hermes' agent loop does.

    For each call: pre_tool_call (block -> veto + blocked post, or proceed),
    then post_tool_call, then transform_tool_result (executed calls only).
    Returns (ctx, guard, records) where records is a list of
    ("ran", transformed_result_or_None) / ("blocked", message).
    """

    def _drive(script, settings=None, session="s1", turn="t1", guard=None):
        if guard is None:
            ctx, guard = make_guard(settings)
        else:
            ctx = None
        hooks = guard.ctx.hooks
        records = []
        for i, (tool, args, result, status, err_type, err_msg) in enumerate(script):
            call_id = f"{tool}-{i}"
            pre = hooks["pre_tool_call"](
                tool_name=tool, session_id=session, turn_id=turn
            )
            if pre:
                records.append(("blocked", pre["message"]))
                hooks["post_tool_call"](
                    tool_name=tool, args=args, result=None,
                    session_id=session, turn_id=turn, tool_call_id=call_id,
                    status="blocked",
                )
                continue
            hooks["post_tool_call"](
                tool_name=tool, args=args, result=result,
                session_id=session, turn_id=turn, tool_call_id=call_id,
                status=status, error_type=err_type, error_message=err_msg,
            )
            transformed = hooks["transform_tool_result"](
                tool_name=tool, result=result,
                session_id=session, turn_id=turn, tool_call_id=call_id,
            )
            records.append(("ran", transformed))
        return ctx, guard, records

    return _drive


def ok(tool, args, result="ok"):
    return (tool, args, result, "ok", None, None)


def err(tool, args, err_type, err_msg):
    return (tool, args, None, "error", err_type, err_msg)


@pytest.fixture
def ev():
    """Build ToolEvents using the real normalization + fingerprinting."""

    def _ev(
        tool,
        args=None,
        result="",
        status="ok",
        error_type=None,
        error_message=None,
        is_mutation=False,
        is_poll=False,
        call_id="",
        family=None,
        canonical_action="",
        mutation_landed=False,
        material=False,
        poll_pct=None,
        poll_done=False,
    ):
        from hermes_plugins.progress_guard import fingerprint, normalize
        from hermes_plugins.progress_guard.events import ToolEvent
        from hermes_plugins.progress_guard.families import classify_action

        # default ignored fields for fingerprinting parity with the guard
        ignored = ("timestamp", "request_id", "trace_id")
        afp = fingerprint.action_fingerprint(tool, normalize.normalize_args(args, ignored))
        norm_result = normalize.normalize_result(result)
        rfp = fingerprint.result_fingerprint(norm_result)
        return ToolEvent(
            tool_name=tool,
            args_fingerprint=afp,
            result_fingerprint=rfp,
            status=status,
            error_class=normalize.error_class(error_type, error_message)
            if status == "error"
            else None,
            failure_sig=normalize.failure_signature(error_type, error_message, norm_result)
            if status == "error"
            else None,
            is_mutation=is_mutation,
            is_poll=is_poll,
            tool_call_id=call_id,
            family=family if family is not None else classify_action(tool, args),
            canonical_action=canonical_action,
            mutation_landed=mutation_landed,
            material_progress=material,
            poll_pct=poll_pct,
            poll_done=poll_done,
        )

    return _ev
