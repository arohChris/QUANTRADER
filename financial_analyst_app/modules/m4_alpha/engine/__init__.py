"""M4.6 engine package — the AlphaEngine loop (see loop.py)."""
from .loop import AlphaEngine, CandleOutcome, SignalSink, StrategyFailure

__all__ = ["AlphaEngine", "CandleOutcome", "SignalSink", "StrategyFailure"]
