"""Desired-state automation engine for SCCS."""

from .world import WorldStore
from .policy import desired_outputs
from .precedence import resolve_light, resolve_screen
from .reconcile import Reconciler
from .explain import build_explain_snapshot, source_label
from .config_validate import ConfigValidationError, validate_compiled_config

__all__ = [
    "WorldStore",
    "desired_outputs",
    "resolve_light",
    "resolve_screen",
    "Reconciler",
    "build_explain_snapshot",
    "source_label",
    "ConfigValidationError",
    "validate_compiled_config",
]