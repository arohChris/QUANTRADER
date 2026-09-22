"""
M1.3 — Feature Flags — config/system/feature_flags.yaml — RESOLVED v1.4: hot-reload via SIGHUP
INSTITUTIONAL HEDGE FUND GRADE — RESOLVED: SIGHUP hot-reload (no redeploy)

WHY flags not code? Changing a flag should not require redeploy.
  M4 strategy_registry checks `is_enabled("enable_stat_arb")` — false in prod, true in paper.

WHY hot-reload via SIGHUP (v1.4 decision)? A strategy kill switch that needs redeploy defeats its purpose,
  especially for risk-driven disables. SIGHUP → reload without dropping connections.

Usage:
    from shared.core.feature_flags import is_enabled, install_sighup_handler
    if not is_enabled("enable_stat_arb"):
        return  # skip stat_arb strategy
    # At startup: install_sighup_handler() — or lifecycle does it
"""
from __future__ import annotations

import logging
import signal
from typing import Any

import yaml

from .config import CONFIG_ROOT

logger = logging.getLogger(__name__)

_FLAGS: dict[str, Any] | None = None
_FLAGS_PATH = CONFIG_ROOT / "system" / "feature_flags.yaml"
_SIGHUP_INSTALLED = False


def _load() -> dict[str, Any]:
    global _FLAGS
    if _FLAGS is not None:
        return _FLAGS
    if not _FLAGS_PATH.exists():
        _FLAGS = {}
        return _FLAGS
    try:
        with open(_FLAGS_PATH, encoding="utf-8") as f:
            _FLAGS = yaml.safe_load(f) or {}
            if not isinstance(_FLAGS, dict):
                _FLAGS = {}
    except yaml.YAMLError:
        _FLAGS = {}
    return _FLAGS


def is_enabled(name: str, default: bool = False) -> bool:
    """Check flag — WHY default False? New flags are off until explicitly enabled (safe)."""
    flags = _load()
    return bool(flags.get(name, default))


def reload() -> None:
    """Force reload from disk — for SIGHUP or tests. Logs diff for audit."""
    global _FLAGS
    old = dict(_FLAGS) if _FLAGS is not None else {}
    _FLAGS = None
    new = _load()
    if old != new:
        diff = {k: (old.get(k), new.get(k)) for k in set(old) | set(new) if old.get(k) != new.get(k)}
        logger.info("feature_flags.reloaded", extra={"diff": diff})
    else:
        logger.debug("feature_flags.reload_noop")


def all_flags() -> dict[str, Any]:
    return dict(_load())


def install_sighup_handler() -> None:
    """
    Install SIGHUP → reload. WHY idempotent? Lifecycle may call twice.
    Called by Lifecycle.startup() or app/main.py lifespan.
    """
    global _SIGHUP_INSTALLED
    if _SIGHUP_INSTALLED:
        return
    try:
        def _on_sighup(signum, frame):  # noqa: ARG001
            logger.info("feature_flags.sighup_received", extra={"signum": signum})
            reload()

        signal.signal(signal.SIGHUP, _on_sighup)
        _SIGHUP_INSTALLED = True
        logger.info("feature_flags.sighup_installed")
    except (ValueError, OSError) as exc:
        logger.warning("feature_flags.sighup_install_failed", extra={"error": str(exc)})

# Backward compat alias
reload_flags = reload
