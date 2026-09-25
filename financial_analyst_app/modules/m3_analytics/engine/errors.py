"""WHY: typed error hierarchy for the Feature Engine (spec m3.1 REV3 §Contracts) —
data-path failures (provider/window/guards) are distinguishable from bugs so callers
never mistake a failed fetch for an empty-success."""
from __future__ import annotations


class FeatureEngineError(RuntimeError):
    """Base for all Feature Engine failures."""


class FeatureEngineDataError(FeatureEngineError):
    """Provider/window/series failures — always typed, never an empty-success."""
