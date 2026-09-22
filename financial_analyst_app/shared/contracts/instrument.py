"""
M2b / shared — AssetClass — Locked vocabulary for instrument asset class.
WHY a frozen contract? M2b instrument master, M2.2 normalizer, M3 get_profile(asset_class),
M5/M6/M7 all classify instruments by asset class. Free-string asset_class invites typos
("forex" vs "Fx" vs "fx") that silently disable a whole analytics profile or risk limit.
Enum = compile-time + runtime validation; unknown value fails loudly at YAML load / P06 gate.

Consumers: M2b (instrument_master, reference_validator), M2.2, M3, M5, M6, M7, M8.
Version: 2.0 frozen — index/equity/synthetic removed (human sign-off 2026-09-22); only forex|crypto|commodity.
"""
from __future__ import annotations

from enum import Enum


class AssetClass(str, Enum):
    """
    Canonical asset-class labels — MUST match config/assets/*.yaml `asset_class:` field
    and the filename stem used by M3 get_profile (commodities.yaml → asset_class "commodity"
    lives in entries; profile loader maps commodity → commodities.yaml — see M2b open note).
    """

    forex = "forex"
    crypto = "crypto"
    commodity = "commodity"

    @classmethod
    def from_yaml_value(cls, raw: str) -> "AssetClass":
        """Validate a YAML asset_class string; raise ValueError with field name on unknown."""
        try:
            return cls(raw)
        except ValueError as exc:
            raise ValueError(f"unknown asset_class {raw!r} — allowed: {[m.value for m in cls]}") from exc
