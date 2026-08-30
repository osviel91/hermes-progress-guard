"""progress-guard — deterministic anti-stall plugin for Hermes Agent.

Plugin entrypoint. ``register(ctx)`` is called by the Hermes plugin loader;
it wires the Progress Guard instance onto the hooks declared in plugin.yaml.
"""

from __future__ import annotations

from .hooks import ProgressGuard


def register(ctx) -> None:
    guard = ProgressGuard(ctx)
    guard.install(ctx)
