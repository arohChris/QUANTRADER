"""
M1.2 — Logging — One JSON line with correlation_id
INSTITUTIONAL HEDGE FUND GRADE — why JSON? Why correlation_id?

WHY JSON not print()? Grep correlation_id=123 finds entire tick→ledger trail across 10 modules.
WHY structlog? Enforces key-value pairs, not f-strings that lose context.
WHY correlation_id on every log? Without it, debugging a single trade across 12 async modules
     is needle-in-haystack. With it, `grep corr=abc123` gives the full story.

Usage:
    from shared.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("risk_decision", approved=True, qty=0.4, correlation_id=str(trade_plan.correlation_id))
    # NEVER: logger.info(f"qty {qty}") without correlation_id
"""
from __future__ import annotations

import logging
import sys
from typing import Any

# Try structlog if available, fallback to stdlib JSON formatter
try:
    import structlog

    HAS_STRUCTLOG = True
except ImportError:
    HAS_STRUCTLOG = False

import json
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Fallback JSON formatter when structlog not installed — always works."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Include extra fields (correlation_id, engine, etc.)
        for key, value in record.__dict__.items():
            if key not in ("name", "msg", "args", "levelname", "levelno", "pathname", "filename",
                           "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                           "created", "msecs", "relativeCreated", "thread", "threadName",
                           "processName", "process", "message", "asctime"):
                # Only include serializable extras
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)
        return json.dumps(payload)


def get_logger(name: str = __name__) -> logging.Logger:
    """
    WHY get_logger not global logger? Per-module name lets M12 filter `engine=data` vs `engine=execution`.
    """
    if HAS_STRUCTLOG:
        # Configure structlog once
        if not structlog.is_configured():
            structlog.configure(
                processors=[
                    structlog.contextvars.merge_contextvars,
                    structlog.processors.add_log_level,
                    structlog.processors.TimeStamper(fmt="iso"),
                    structlog.processors.JSONRenderer(),
                ],
                wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
                context_class=dict,
                logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
                cache_logger_on_first_use=True,
            )
        return structlog.get_logger(name)  # type: ignore[return-value]
    # Fallback — stdlib with JSON
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


# Convenience — re-export for `from shared.core.logging import logger`
logger = get_logger("app")
