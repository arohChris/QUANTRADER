"""WHY: M4.2 D5 `donchian_breakout` family (A27 id) — long/short Donchian channel
breaks, but only when chopiness says a trend regime can carry the break; inside
the prior range there is no breakout, and chop regimes filter them out
(bible line: chop < 38 = trending).

Pure compute: evaluate(features, params) -> signed strength [-1, +1]
(contract: docs/specs/m4.2_strategy_files.spec.md).
"""
from __future__ import annotations

import math

NAME = "donchian_breakout"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {
    "chan": ("donchian_20", {}),
    "chop": ("choppiness", {}),
}
PARAM_DEFAULTS: dict[str, float] = {"chop_max": 38.0}


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
    chop_max = params.get("chop_max", PARAM_DEFAULTS["chop_max"])
    if not math.isfinite(chop_max) or not 0.0 <= chop_max <= 100.0:
        raise ValueError(
            f"params.chop_max: must be in [0, 100], got {chop_max!r}"
        )
    chan = _slot(features, "chan")
    chop = _slot(features, "chop")

    if chop >= chop_max:
        return 0.0  # chop regime: breakouts lack trend follow-through
    if chan > 100.0:
        progress = (chan - 100.0) / 100.0
        return 0.5 + 0.5 * min(1.0, progress)  # house ramp from the 100% break
    if chan < 0.0:
        progress = (0.0 - chan) / 100.0
        return -(0.5 + 0.5 * min(1.0, progress))
    return 0.0  # inside the prior range: no breakout
