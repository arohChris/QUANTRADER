"""WHY: M4.2 A27-id `microstructure_flow` — order-book imbalance tilt: sustained
bid/ask depth skew >= long threshold -> long flow, <= short threshold -> short
flow, inside the dead band -> nothing. Capability-gated: needs
order_book_imbalance which is UNAVAILABLE on deriv (M3.0 gate returns False), so
the file exists and is testable but can never be selected on Deriv.

Pure compute: evaluate(features, params) -> signed strength [-1, +1]
(contract: docs/specs/m4.2_strategy_files.spec.md).
"""
from __future__ import annotations

import math

NAME = "microstructure_flow"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {"imb": ("order_book_imbalance", {})}
PARAM_DEFAULTS: dict[str, float] = {"long": 0.6, "short": -0.6}


def _slot(features: dict[str, float], slot: str) -> float:
    if slot not in features:
        raise ValueError(f"features: missing slot {slot!r}")
    value = features[slot]
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"features.{slot}: must be a finite number, got {value!r}")
    return float(value)


def evaluate(features: dict[str, float], params: dict[str, float]) -> float:
    if features is None:
        raise ValueError("features: must be a mapping, got None")
    if params is None:
        raise ValueError("params: must be a mapping, got None")
    long = params.get("long", PARAM_DEFAULTS["long"])
    if not math.isfinite(long) or not 0.0 <= long < 1.0:
        raise ValueError(
            f"params.long: must be in [0, 1), got {long!r}"
        )
    short = params.get("short", PARAM_DEFAULTS["short"])
    if not math.isfinite(short) or not -1.0 < short <= 0.0 or short >= long:
        raise ValueError(
            f"params.short: must be in (-1, 0] and < long, "
            f"got short={short!r} long={long!r}"
        )
    imb = _slot(features, "imb")

    if imb >= long:
        progress = (imb - long) / (1.0 - long)
        return 0.5 + 0.5 * min(1.0, progress)  # house ramp from the tilt threshold
    if imb <= short:
        progress = (short - imb) / (1.0 + short)
        return -(0.5 + 0.5 * min(1.0, progress))
    return 0.0  # dead band: book tilt too weak to act on
