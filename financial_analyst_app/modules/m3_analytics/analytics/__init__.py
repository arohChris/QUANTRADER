"""D4 layout — m3_analytics.analytics: ONE FILE per supported analytic.

Each analytic = its calculation + its own requirements/manifest (e.g. rsi.py,
ema.py, atr.py, adx.py, macd.py, bbands.py — NAME, REQUIREMENTS, compute).
Day-1: package intentionally EMPTY (M3.2/P17 owns the files; M3.1 registry
scans this package — empty must scan as empty, never crash).
"""
