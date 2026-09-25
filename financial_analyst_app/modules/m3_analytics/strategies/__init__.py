"""D4 layout — m3_analytics.strategies: ONE FILE per supported strategy.

Each strategy = its logic + its requirements (FEATURE_SLOTS + PARAM_DEFAULTS).
M4.2 (blanket LGTM 2026-09-25) landed the 7 price-only Day-1 files; contract is
PROVISIONAL until the M4.1 registry consumes it (spec
docs/specs/m4.2_strategy_files.spec.md).
cross_sectional_momentum + statistical_arbitrage stay OUT until P19/M3.3
cross-asset analytics (correlation/hedge_ratio/stationarity) exist.
OPEN (flagged, not silent): strategies/ placement — inside m3_analytics per
human D4 vs bible M4 Strategy Engine; M4.1 re-decides at registry time.
"""
