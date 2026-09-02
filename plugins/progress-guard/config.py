"""Progress Guard configuration — conservative defaults, fully overridable.

Settings live under ``plugins.entries.progress-guard.settings`` in config.yaml
(read through ``ctx.get_config``), mirroring the handoff schema:

    enabled, debug,
    exact_repeat.{enabled, window, threshold},
    identical_result.{enabled, threshold},
    cycle.{enabled, window, max_cycle_length, repetitions},
    repeated_failure.{enabled, threshold},
    reasoning_loop.{enabled, threshold},
    policy.{warn_score, recover_score, block_score},
    recovery.max_attempts,
    normalization.ignored_fields

Environment overrides (``PROGRESS_GUARD_*``) are honored for quick tuning.
Defaults prefer false negatives over false positives.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _as_str_tuple(value: Any, default: Iterable[str]) -> tuple:
    if isinstance(value, (list, tuple, set)):
        return tuple(str(v) for v in value)
    return tuple(default)


@dataclass
class ExactRepeatConfig:
    enabled: bool = True
    window: int = 8
    threshold: int = 3

    @classmethod
    def from_mapping(cls, m: Any) -> "ExactRepeatConfig":
        m = m if isinstance(m, dict) else {}
        return cls(
            enabled=_as_bool(m.get("enabled"), True),
            window=_as_int(m.get("window"), 8),
            threshold=_as_int(m.get("threshold"), 3),
        )


@dataclass
class IdenticalResultConfig:
    enabled: bool = True
    threshold: int = 3

    @classmethod
    def from_mapping(cls, m: Any) -> "IdenticalResultConfig":
        m = m if isinstance(m, dict) else {}
        return cls(
            enabled=_as_bool(m.get("enabled"), True),
            threshold=_as_int(m.get("threshold"), 3),
        )


@dataclass
class CycleConfig:
    enabled: bool = True
    window: int = 10
    max_cycle_length: int = 3
    repetitions: int = 2

    @classmethod
    def from_mapping(cls, m: Any) -> "CycleConfig":
        m = m if isinstance(m, dict) else {}
        return cls(
            enabled=_as_bool(m.get("enabled"), True),
            window=_as_int(m.get("window"), 10),
            max_cycle_length=_as_int(m.get("max_cycle_length"), 3),
            repetitions=_as_int(m.get("repetitions"), 2),
        )


@dataclass
class RepeatedFailureConfig:
    enabled: bool = True
    threshold: int = 3

    @classmethod
    def from_mapping(cls, m: Any) -> "RepeatedFailureConfig":
        m = m if isinstance(m, dict) else {}
        return cls(
            enabled=_as_bool(m.get("enabled"), True),
            threshold=_as_int(m.get("threshold"), 3),
        )


@dataclass
class ReasoningLoopConfig:
    """Repeated identical reasoning segments while the LLM streams thinking.

    Catches pure thinking loops that emit no tool calls, and ABAB/ABCABC
    cycles over normalized reasoning blocks within one generation. Requires
    the global Hermes opt-in ``plugins.stream_reasoning_deltas: true`` for
    reasoning deltas to reach ``on_stream_delta`` at all; without it this
    detector is inert (safe default).
    """

    enabled: bool = True
    threshold: int = 3
    cycle_repetitions: int = 2
    max_cycle_length: int = 3

    @classmethod
    def from_mapping(cls, m: Any) -> "ReasoningLoopConfig":
        m = m if isinstance(m, dict) else {}
        return cls(
            enabled=_as_bool(m.get("enabled"), True),
            threshold=_as_int(m.get("threshold"), 3),
            cycle_repetitions=_as_int(m.get("cycle_repetitions"), 2),
            max_cycle_length=_as_int(m.get("max_cycle_length"), 3),
        )


@dataclass
class PolicyConfig:
    warn_score: int = 3
    recover_score: int = 5
    block_score: int = 7

    @classmethod
    def from_mapping(cls, m: Any) -> "PolicyConfig":
        m = m if isinstance(m, dict) else {}
        return cls(
            warn_score=_as_int(m.get("warn_score"), 3),
            recover_score=_as_int(m.get("recover_score"), 5),
            block_score=_as_int(m.get("block_score"), 7),
        )


@dataclass
class RecoveryConfig:
    max_attempts: int = 2

    @classmethod
    def from_mapping(cls, m: Any) -> "RecoveryConfig":
        m = m if isinstance(m, dict) else {}
        return cls(max_attempts=_as_int(m.get("max_attempts"), 2))


@dataclass
class FamilyCycleConfig:
    """Cycle detection over action-family trajectories (READ/SEARCH/...)."""

    enabled: bool = True
    window: int = 10
    max_cycle_length: int = 3
    repetitions: int = 2

    @classmethod
    def from_mapping(cls, m: Any) -> "FamilyCycleConfig":
        m = m if isinstance(m, dict) else {}
        return cls(
            enabled=_as_bool(m.get("enabled"), True),
            window=_as_int(m.get("window"), 10),
            max_cycle_length=_as_int(m.get("max_cycle_length"), 3),
            repetitions=_as_int(m.get("repetitions"), 2),
        )


@dataclass
class StepsConfig:
    """Steps-since-material-progress as an independent stagnation signal.

    Tracks actions since the last material-progress event. By itself it does
    not trigger recovery (exploration with no detector evidence must not stall);
    it amplifies detector evidence when the threshold is exceeded and is
    surfaced in metrics, debug lines and recovery messages.
    """

    enabled: bool = True
    bonus_threshold: int = 6
    bonus_delta: int = 1

    @classmethod
    def from_mapping(cls, m: Any) -> "StepsConfig":
        m = m if isinstance(m, dict) else {}
        return cls(
            enabled=_as_bool(m.get("enabled"), True),
            bonus_threshold=_as_int(m.get("bonus_threshold"), 6),
            bonus_delta=_as_int(m.get("bonus_delta"), 1),
        )


@dataclass
class MaterialProgressConfig:
    """Whether material-progress assessment is active at all."""

    enabled: bool = True

    @classmethod
    def from_mapping(cls, m: Any) -> "MaterialProgressConfig":
        m = m if isinstance(m, dict) else {}
        return cls(enabled=_as_bool(m.get("enabled"), True))


@dataclass
class CanonicalConfig:
    """Semantic-lite canonical action keys (no embeddings)."""

    enabled: bool = True

    @classmethod
    def from_mapping(cls, m: Any) -> "CanonicalConfig":
        m = m if isinstance(m, dict) else {}
        return cls(enabled=_as_bool(m.get("enabled"), True))


DEFAULT_IGNORED_FIELDS = ("timestamp", "request_id", "trace_id")


@dataclass
class ProgressGuardConfig:
    enabled: bool = True
    debug: bool = False
    exact_repeat: ExactRepeatConfig = field(default_factory=ExactRepeatConfig)
    identical_result: IdenticalResultConfig = field(default_factory=IdenticalResultConfig)
    cycle: CycleConfig = field(default_factory=CycleConfig)
    family_cycle: FamilyCycleConfig = field(default_factory=FamilyCycleConfig)
    repeated_failure: RepeatedFailureConfig = field(default_factory=RepeatedFailureConfig)
    reasoning_loop: ReasoningLoopConfig = field(default_factory=ReasoningLoopConfig)
    steps: StepsConfig = field(default_factory=StepsConfig)
    material_progress: MaterialProgressConfig = field(default_factory=MaterialProgressConfig)
    canonical: CanonicalConfig = field(default_factory=CanonicalConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    ignored_fields: tuple = field(default_factory=lambda: DEFAULT_IGNORED_FIELDS)

    @classmethod
    def from_mapping(cls, mapping: Optional[dict]) -> "ProgressGuardConfig":
        m = mapping if isinstance(mapping, dict) else {}
        normalization = m.get("normalization") if isinstance(m.get("normalization"), dict) else {}
        cfg = cls(
            enabled=_as_bool(m.get("enabled"), True),
            debug=_as_bool(m.get("debug"), False),
            exact_repeat=ExactRepeatConfig.from_mapping(m.get("exact_repeat")),
            identical_result=IdenticalResultConfig.from_mapping(m.get("identical_result")),
            cycle=CycleConfig.from_mapping(m.get("cycle")),
            family_cycle=FamilyCycleConfig.from_mapping(m.get("family_cycle")),
            repeated_failure=RepeatedFailureConfig.from_mapping(m.get("repeated_failure")),
            reasoning_loop=ReasoningLoopConfig.from_mapping(m.get("reasoning_loop")),
            steps=StepsConfig.from_mapping(m.get("steps")),
            material_progress=MaterialProgressConfig.from_mapping(
                m.get("material_progress")
            ),
            canonical=CanonicalConfig.from_mapping(m.get("canonical")),
            policy=PolicyConfig.from_mapping(m.get("policy")),
            recovery=RecoveryConfig.from_mapping(m.get("recovery")),
            ignored_fields=_as_str_tuple(
                normalization.get("ignored_fields"), DEFAULT_IGNORED_FIELDS
            ),
        )
        cfg._apply_env()
        return cfg

    @classmethod
    def from_ctx(cls, ctx) -> "ProgressGuardConfig":
        if ctx is None:
            return cls.from_mapping(None)
        get = ctx.get_config
        m = {
            "enabled": get("enabled", None),
            "debug": get("debug", None),
            "exact_repeat": {
                k: get(f"exact_repeat.{k}", None)
                for k in ("enabled", "window", "threshold")
            },
            "identical_result": {
                k: get(f"identical_result.{k}", None)
                for k in ("enabled", "threshold")
            },
            "cycle": {
                k: get(f"cycle.{k}", None)
                for k in ("enabled", "window", "max_cycle_length", "repetitions")
            },
            "family_cycle": {
                k: get(f"family_cycle.{k}", None)
                for k in ("enabled", "window", "max_cycle_length", "repetitions")
            },
            "steps": {
                k: get(f"steps.{k}", None)
                for k in ("enabled", "bonus_threshold", "bonus_delta")
            },
            "material_progress": {
                k: get(f"material_progress.{k}", None) for k in ("enabled",)
            },
            "canonical": {k: get(f"canonical.{k}", None) for k in ("enabled",)},
            "reasoning_loop": {
                k: get(f"reasoning_loop.{k}", None)
                for k in (
                    "enabled", "threshold",
                    "cycle_repetitions", "max_cycle_length",
                )
            },
            "policy": {
                k: get(f"policy.{k}", None)
                for k in ("warn_score", "recover_score", "block_score")
            },
            "recovery": {k: get(f"recovery.{k}", None) for k in ("max_attempts",)},
            "normalization": {"ignored_fields": get("normalization.ignored_fields", None)},
        }
        return cls.from_mapping(m)

    def _apply_env(self) -> None:
        self.enabled = _as_bool(os.environ.get("PROGRESS_GUARD_ENABLED"), self.enabled)
        self.debug = _as_bool(os.environ.get("PROGRESS_GUARD_DEBUG"), self.debug)
        self.recovery.max_attempts = _as_int(
            os.environ.get("PROGRESS_GUARD_MAX_ATTEMPTS"), self.recovery.max_attempts
        )
        self.policy.recover_score = _as_int(
            os.environ.get("PROGRESS_GUARD_RECOVER_SCORE"), self.policy.recover_score
        )
        self.policy.block_score = _as_int(
            os.environ.get("PROGRESS_GUARD_BLOCK_SCORE"), self.policy.block_score
        )
