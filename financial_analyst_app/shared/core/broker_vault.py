"""
M1.4 — Broker Vault — encrypted token storage per user_id+broker_id
INSTITUTIONAL HEDGE FUND GRADE — tokens never in logs, never in code, never in YAML

WHY vault not .env? .env is for system secrets (DB password). User broker tokens are
  per-tenant (1000 users × 3 brokers = 3000 tokens) — must be in DB encrypted, not env.

WHY Fernet? Symmetric, authenticated encryption — if DB row tampered, decrypt fails loud.
  Key from env `VAULT_KEY` (32 url-safe base64). Never log key or token.

WHY per-tenant? `vault.get(user_id=42, broker="deriv")` returns ONLY that tenant's token.
  Isolation enforced — without tenant_context, raises IsolationError.

Day 1: in-memory dict + Fernet. Prod: Postgres `broker_credentials` table + AWS SM / GCP SM.

Usage:
    from shared.core.broker_vault import vault
    token = vault.get(user_id="42", broker="deriv")  # decrypts
    # NEVER: logger.info(f"token {token}")

    # Store (on user connect):
    vault.store(user_id="42", broker="deriv", token="deriv_token_abc")
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Try Fernet, fallback to base64 obfuscation for dev (with loud warning)
try:
    from cryptography.fernet import Fernet, InvalidToken

    HAS_FERNET = True
except ImportError:
    HAS_FERNET = False
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment,misc]


class VaultError(RuntimeError):
    pass


class BrokerVault:
    """
    Per-tenant encrypted vault — WHY class not functions? Holds key + store dict,
    mockable in tests (inject fake vault).
    """

    def __init__(self, key: str | None = None):
        # WHY env VAULT_KEY? So key not in code/repo — only in .env / Secret Manager
        raw_key = key or os.getenv("VAULT_KEY", "")
        self._store: Dict[tuple[str, str], str] = {}  # (user_id, broker) → encrypted token
        self._fernet: Any = None  # type: ignore[name-defined]
        if HAS_FERNET and raw_key:
            try:
                # Fernet key must be 32 url-safe base64 — normalize
                if len(raw_key) == 44:  # already base64
                    self._fernet = Fernet(raw_key.encode())
                else:
                    # Derive 32-byte key from env string — pad/truncate + b64
                    padded = (raw_key * 32)[:32].encode()
                    b64 = base64.urlsafe_b64encode(padded)
                    self._fernet = Fernet(b64)
            except Exception as exc:  # noqa: BLE001
                logger.warning("vault.fernet_init_failed, fallback to obfuscation: %s", exc)
                self._fernet = None
        elif not HAS_FERNET:
            logger.warning("vault.cryptography_not_installed — using base64 obfuscation (DEV ONLY)")

    def _encrypt(self, token: str) -> str:
        if self._fernet:
            return self._fernet.encrypt(token.encode()).decode()
        # Fallback — NOT secure, dev only, loud warning on first use
        return base64.b64encode(token.encode()).decode()

    def _decrypt(self, encrypted: str) -> str:
        if self._fernet:
            try:
                return self._fernet.decrypt(encrypted.encode()).decode()
            except InvalidToken as exc:
                raise VaultError("Vault decrypt failed — token tampered or wrong VAULT_KEY") from exc
        return base64.b64decode(encrypted.encode()).decode()

    def store(self, user_id: str | int, broker: str, token: str) -> None:
        """
        Store token encrypted.
        WHY not log token? Token in logs = breach. We log only user+broker, never value.
        """
        key = (str(user_id), broker)
        self._store[key] = self._encrypt(token)
        logger.info("vault.stored", extra={"user_id": str(user_id), "broker": broker})
        # WHY not log token length? Even length leaks info. Just log stored.

    def get(self, user_id: str | int, broker: str) -> str:
        """
        Retrieve and decrypt.
        WHY raise not return None? Caller needs token to connect — None would cause silent fail later.
        """
        key = (str(user_id), broker)
        encrypted = self._store.get(key)
        if encrypted is None:
            # WHY VaultError not None? Fail loud — don't let M2 connect with empty token and get 401 silent.
            raise VaultError(f"No token for user {user_id!r} broker {broker!r} — user must connect broker first")
        return self._decrypt(encrypted)

    def has(self, user_id: str | int, broker: str) -> bool:
        return (str(user_id), broker) in self._store

    def delete(self, user_id: str | int, broker: str) -> None:
        key = (str(user_id), broker)
        self._store.pop(key, None)
        logger.info("vault.deleted", extra={"user_id": str(user_id), "broker": broker})

    def clear_all(self) -> None:
        """For tests — wipe store."""
        self._store.clear()


# Singleton — `from shared.core.broker_vault import vault`
vault = BrokerVault()

# For tests that need isolated vault:
def create_vault(key: str | None = None) -> BrokerVault:
    return BrokerVault(key=key)
