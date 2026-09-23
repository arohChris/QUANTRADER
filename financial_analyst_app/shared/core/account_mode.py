"""
M1.3 AccountGateService — 2-gate: MODE (demo|live) + AUTO (OFF|ON).
WHY two gates: LIVE selects which ledger/credentials; AUTO whether bot may call M6.
  LIVE+OFF = armed but paused (institutional default). Fresh process → DEMO+OFF (fail-closed).
WHY singleton: bot and API must share one atomic view of both gates under one lock.
No secrets — mode/auto strings only; tokens stay env/vault (accounts.yaml / M6 vault).
Spec: docs/specs/m1.3_account_mode_two_gate.spec.md (LGTM 2026-09-23)
"""
from __future__ import annotations

import os
import threading
from typing import Literal, Mapping

from shared.core.config import AccountsConfig
from shared.core.exceptions import BaseAppError
from shared.contracts.errors import ErrorCode

AccountMode = Literal["demo", "live"]
AutoTrade = Literal["OFF", "ON"]
_MODES: frozenset[str] = frozenset({"demo", "live"})
_AUTOS: frozenset[str] = frozenset({"off", "on"})  # input normalized lower


class InvalidAccountModeError(BaseAppError, ValueError):
    """API → 422. Invalid demo|live string. ValueError sub for callers that catch it."""

    error_code = ErrorCode.config_error
    status_code = 422


class InvalidAutoTradeError(BaseAppError, ValueError):
    """API → 422. Invalid OFF|ON string. ValueError sub for callers that catch it."""

    error_code = ErrorCode.config_error
    status_code = 422


class LiveNotArmedError(BaseAppError, ValueError):
    """API → 409. live not armed (set_mode live) or AUTO ON while mode==live and not armed."""

    error_code = ErrorCode.config_error
    status_code = 409


class AccountGateService:
    """Thread-safe 2-gate runtime. Critical section: validate → arm check → write field+source."""

    def __init__(
        self,
        accounts: AccountsConfig | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._accounts = accounts if accounts is not None else AccountsConfig()
        self._lock = threading.Lock()
        self._mode: AccountMode = "demo"
        self._auto: AutoTrade = "OFF"
        self._source: str = "default"
        self._auto_source: str = "default"
        seed = (env if env is not None else os.environ).get("ACCOUNT_MODE", "").strip().lower()
        # Invalid/empty → treat as unset: demo + default (no bypass of arm gate).
        if seed == "live" and self._live_armed():
            self._mode = "live"
            self._source = "env"
        elif seed == "demo":
            self._mode = "demo"
            self._source = "env"
        # ACCOUNT_MODE=live + not armed → stay demo + default (spec boot tree).
        # AUTO has no env seed Day-1 — always OFF + default.

    @property
    def source(self) -> str:
        return self._source

    def _live_armed(self) -> bool:
        for b in self._accounts.brokers.values():
            if b.live.enabled:
                return True
        return False

    def live_armed(self) -> bool:
        return self._live_armed()

    def get_mode(self) -> AccountMode:
        with self._lock:
            return self._mode

    def get_auto(self) -> AutoTrade:
        with self._lock:
            return self._auto

    def set_mode(self, mode: str) -> AccountMode:
        """Switch demo|live. live requires arm; demo always OK. Typed errors only."""
        m = str(mode).strip().lower()
        if m not in _MODES:
            raise InvalidAccountModeError(f"invalid account mode: {mode!r} (must be demo|live)")
        with self._lock:
            if m == "live" and not self._live_armed():
                raise LiveNotArmedError(
                    "live mode not armed — set live.enabled: true in config/accounts.yaml"
                )
            self._mode = m  # type: ignore[assignment]
            self._source = "api"
            return self._mode

    def set_auto(self, auto: str) -> AutoTrade:
        """Switch OFF|ON. AUTO ON allowed at DEMO (paper). At LIVE requires live_armed."""
        a = str(auto).strip().lower()
        if a not in _AUTOS:
            raise InvalidAutoTradeError(f"invalid auto-trade: {auto!r} (must be OFF|ON)")
        value: AutoTrade = "ON" if a == "on" else "OFF"
        with self._lock:
            if value == "ON" and self._mode == "live" and not self._live_armed():
                raise LiveNotArmedError(
                    "auto ON at live requires live.enabled arm — stay OFF or arm live"
                )
            self._auto = value
            self._auto_source = "api"
            return self._auto

    def snapshot(self) -> dict[str, object]:
        """Consistent read of both gates + derived bot_will_trade under one lock."""
        with self._lock:
            armed = self._live_armed()
            return {
                "mode": self._mode,
                "auto": self._auto,
                "source": self._source,
                "auto_source": self._auto_source,
                "live_armed": armed,
                "bot_will_trade": self._mode == "live" and self._auto == "ON",
            }


# Process singleton — API and bot both import this (A4: one atomic service).
_service: AccountGateService | None = None
_service_lock = threading.Lock()


def get_account_mode_service(accounts: AccountsConfig | None = None) -> AccountGateService:
    global _service
    with _service_lock:
        if _service is None:
            _service = AccountGateService(accounts=accounts)
        return _service


def install_account_mode_service(svc: AccountGateService) -> AccountGateService:
    """Bind a fresh service as the process singleton (create_app / tests)."""
    global _service
    with _service_lock:
        _service = svc
        return svc


def reset_account_mode_service_for_tests() -> None:
    """Tests only — drop singleton so each test gets a fresh default demo+OFF."""
    global _service
    with _service_lock:
        _service = None


# Back-compat alias — old 1-gate name; prefer AccountGateService in new code.
AccountModeService = AccountGateService
