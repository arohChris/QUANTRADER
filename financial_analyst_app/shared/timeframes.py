"""WHY: single timeframe source of truth (ACTION 35/36 canon) — one registry, not four
competing vocabularies (M3.1 draft's wrong `1d`, hot.py comment, m2.9 regex drift).

Seeded verbatim from M2.4 `candles._INTERVAL_MINUTES` and drift-locked by
`test_tf_registry_parity_with_m2_candles`. `shared/` may NEVER import `modules/`
(dependency law) — so this is a copy, proven equal by that test.

Calendar buckets (`1w`, `1mo`, `1y`) are deliberately ABSENT: fixed-duration strings
only until explicit calendar-bucket semantics are spec'd (spec m3.1 REV3 edge —
never silently treated as fixed-duration candles).
"""
from __future__ import annotations

INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "40m": 40,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "24h": 1440,
}
