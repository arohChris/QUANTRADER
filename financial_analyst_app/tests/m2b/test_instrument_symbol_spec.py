"""
P05 / M2b.1 — Instrument & Symbol Identity — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md §2 Step 3 — tests exist first against docs/specs/m2b.instrument_symbol.md.
Implementation imported only via the facade entry point.

Review notes applied 2026-09-22:
  1) Exact typed-exception asserts (UnknownBrokerError / UnknownInstrumentError / ReferenceValidationError)
  2) Exhaustive I02 round-trip over every YAML broker_native mapping
  3) YAML source-of-truth: mappings loaded from config/assets/*.yaml, not hard-coded
  4) importorskip REMOVED — missing impl must FAIL collection (suite red), never skip to exit 0
     (AGENTS.md §2 Step 6 zero-skips, §10 anti-pattern; review 2026-09-22)

Gate: to_canonical("R_75","deriv") == "SYNTHETIC.VOLATILITY_75"
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Direct import ONLY — no importorskip. Before Step 5 impl exists this collection-fails
# and the suite goes RED (honest "not implemented"); after impl, all tests must pass.
from modules.reference import reference

# Typed errors — frozen contract (M2b.1 review: assert exact type, not bare Exception)
from shared.contracts.errors import (
    ReferenceValidationError,
    UnknownBrokerError,
    UnknownInstrumentError,
)

# --- YAML fixtures (source of truth for I02 + source-of-truth test) ---

_ASSETS_DIR = Path(__file__).resolve().parents[2] / "config" / "assets"
_YAML_FILES = ("forex.yaml", "synthetic.yaml", "crypto.yaml", "commodities.yaml")


def _iter_yaml_mappings():
    """Yield (instrument_id, broker, native) for every explicit broker_native in YAML.

    Handles both shapes from spec:
      broker_native: "R_75"                          → applies to all brokers in entry.brokers
      broker_native: {deriv: frxEURUSD, mt5: EURUSD} → per-broker map
    """
    for fname in _YAML_FILES:
        path = _ASSETS_DIR / fname
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for inst_id, entry in data.items():
            if not isinstance(entry, dict):
                continue
            bn = entry.get("broker_native")
            if bn is None:
                continue
            if isinstance(bn, str):
                for broker in entry.get("brokers", []):
                    yield inst_id, broker, bn
            elif isinstance(bn, dict):
                for broker, native in bn.items():
                    yield inst_id, broker, native


# --- Edges from spec ---

def test_to_canonical_r75_deriv_gate() -> None:
    # P05 gate (bible 02b line 340): R_75 -> SYNTHETIC.VOLATILITY_75
    assert reference.to_canonical("R_75", "deriv") == "SYNTHETIC.VOLATILITY_75"


def test_to_canonical_frxEURUSD_deriv() -> None:
    assert reference.to_canonical("frxEURUSD", "deriv") == "FX.EURUSD"


def test_to_canonical_EURUSD_mt5() -> None:
    assert reference.to_canonical("EURUSD", "mt5") == "FX.EURUSD"


def test_to_native_roundtrip_I02_synthetic() -> None:
    canon = reference.to_native("SYNTHETIC.VOLATILITY_75", "deriv")
    assert canon == "R_75"
    assert reference.to_canonical(canon, "deriv") == "SYNTHETIC.VOLATILITY_75"


def test_to_native_roundtrip_I02_forex_deriv() -> None:
    assert reference.to_native("FX.EURUSD", "deriv") == "frxEURUSD"
    assert reference.to_canonical("frxEURUSD", "deriv") == "FX.EURUSD"


def test_unknown_native_passthrough() -> None:
    # Spec edge: unknown native returns unchanged (M2.3 validates later)
    assert reference.to_canonical("NOT_A_SYMBOL", "deriv") == "NOT_A_SYMBOL"


def test_unknown_broker_raises_typed_error() -> None:
    # Review #1: exact type, not bare Exception
    with pytest.raises(UnknownBrokerError) as exc:
        reference.to_canonical("R_75", "coinbase")
    assert "coinbase" in str(exc.value).lower()


def test_get_instrument_known() -> None:
    inst = reference.get_instrument("FX.EURUSD")
    assert inst["asset_class"] == "forex"
    assert inst["canonical_symbol"] == "EURUSD"
    assert inst["display_name"] == "EURUSD"
    assert "deriv" in inst["brokers"]


def test_get_instrument_unknown_raises() -> None:
    # Review #1: exact type
    with pytest.raises(UnknownInstrumentError) as exc:
        reference.get_instrument("NOPE.MISSING")
    assert "NOPE.MISSING" in str(exc.value)


def test_exists_with_broker_true() -> None:
    assert reference.exists("FX.EURUSD", broker="deriv") is True
    assert reference.exists("FX.EURUSD", broker="mt5") is True


def test_exists_broker_not_listed_false() -> None:
    # SYNTHETIC only on deriv per synthetic.yaml brokers: [deriv]
    assert reference.exists("SYNTHETIC.VOLATILITY_75", broker="mt5") is False


def test_exists_without_broker() -> None:
    assert reference.exists("FX.EURUSD") is True
    assert reference.exists("NOPE.MISSING") is False


def test_exists_broker_none_vs_listed() -> None:
    assert reference.exists("SYNTHETIC.VOLATILITY_75") is True
    assert reference.exists("SYNTHETIC.VOLATILITY_75", broker=None) is True


def test_case_sensitive_no_silent_lower() -> None:
    # Spec: case-sensitive — r_75 must NOT map
    assert reference.to_canonical("r_75", "deriv") == "r_75"


def test_deterministic_same_input_same_output_I08() -> None:
    a = reference.to_canonical("R_75", "deriv")
    b = reference.to_canonical("R_75", "deriv")
    assert a == b == "SYNTHETIC.VOLATILITY_75"


# --- Adversarial (not in spec edges) ---

def test_adversarial_sql_injection_passthrough() -> None:
    evil = "'; DROP TABLE instruments;--"
    assert reference.to_canonical(evil, "deriv") == evil


def test_adversarial_none_native_raises() -> None:
    # Review #1: ValidationError-style typed error
    with pytest.raises(ReferenceValidationError):
        reference.to_canonical(None, "deriv")  # type: ignore[arg-type]


def test_adversarial_empty_native_or_broker_raises() -> None:
    with pytest.raises(ReferenceValidationError):
        reference.to_canonical("", "deriv")
    with pytest.raises(ReferenceValidationError):
        reference.to_canonical("R_75", "")
    with pytest.raises(UnknownBrokerError):
        reference.to_canonical("R_75", "")  # empty broker → typed broker error (spec edge)


def test_adversarial_mapped_id_not_in_master_detectable() -> None:
    # If a mapping pointed at unknown id, exists() must be False (detectable; P06 validates at publish)
    assert reference.exists("SYNTHETIC.VOLATILITY_75") is True
    assert reference.exists("SYNTHETIC.GHOST") is False


def test_no_user_id_in_cache_key_I06() -> None:
    # Shared truth: same result regardless of any tenant — API takes no user_id
    import inspect
    sig = inspect.signature(reference.to_canonical)
    assert "user_id" not in sig.parameters
    assert "user_id" not in inspect.signature(reference.get_instrument).parameters


# --- Commodity (asset class expansion — defaults 2026-09-22) ---

def test_to_canonical_frxXAUUSD_deriv() -> None:
    assert reference.to_canonical("frxXAUUSD", "deriv") == "COMMODITY.XAUUSD"


def test_to_canonical_XAUUSD_mt5() -> None:
    assert reference.to_canonical("XAUUSD", "mt5") == "COMMODITY.XAUUSD"


def test_to_canonical_XAGUSD_mt5() -> None:
    assert reference.to_canonical("XAGUSD", "mt5") == "COMMODITY.XAGUSD"


def test_to_native_roundtrip_I02_commodity_deriv() -> None:
    native = reference.to_native("COMMODITY.XAUUSD", "deriv")
    assert native == "frxXAUUSD"
    assert reference.to_canonical(native, "deriv") == "COMMODITY.XAUUSD"


def test_to_native_roundtrip_I02_commodity_mt5() -> None:
    native = reference.to_native("COMMODITY.XAGUSD", "mt5")
    assert native == "XAGUSD"
    assert reference.to_canonical(native, "mt5") == "COMMODITY.XAGUSD"


def test_get_instrument_commodity_asset_class() -> None:
    inst = reference.get_instrument("COMMODITY.XAUUSD")
    assert inst["asset_class"] == "commodity"
    assert inst["canonical_symbol"] == "XAUUSD"
    assert inst["display_name"] == "Gold"
    assert "deriv" in inst["brokers"]
    assert "mt5" in inst["brokers"]


def test_exists_commodity_with_broker() -> None:
    assert reference.exists("COMMODITY.XAUUSD", broker="deriv") is True
    assert reference.exists("COMMODITY.XAUUSD", broker="mt5") is True
    # headway not in commodities.yaml brokers list
    assert reference.exists("COMMODITY.XAUUSD", broker="headway") is False


def test_exists_commodity_without_broker() -> None:
    assert reference.exists("COMMODITY.XAGUSD") is True


def test_commodity_case_sensitive_no_silent_lower() -> None:
    # frxXAUUSD must not map if lowercased
    assert reference.to_canonical("frxxauusd", "deriv") == "frxxauusd"


# --- Review #2: exhaustive I02 over every YAML mapping ---

def test_I02_exhaustive_roundtrip_all_yaml_mappings() -> None:
    """for every explicit (instrument_id, broker) in YAML:
         to_canonical(to_native(id, broker), broker) == id
         and to_native(id, broker) == the native recorded in YAML.
    """
    mappings = list(_iter_yaml_mappings())
    assert len(mappings) >= 7, f"expected ≥7 YAML mappings, got {len(mappings)} — check config/assets/*.yaml"
    for inst_id, broker, native in mappings:
        got_native = reference.to_native(inst_id, broker)
        assert got_native == native, (
            f"to_native({inst_id!r}, {broker!r}) = {got_native!r}, YAML says {native!r}"
        )
        # I02 both directions
        assert reference.to_canonical(got_native, broker) == inst_id, (
            f"I02 fail: to_canonical(to_native({inst_id!r}, {broker!r}), {broker!r}) != {inst_id!r}"
        )
        assert reference.to_canonical(native, broker) == inst_id


# --- Review #3: YAML is the source of truth (not hard-coded tables in impl) ---

def test_yaml_is_source_of_truth_mappings_reachable() -> None:
    """Every broker_native in config/assets/*.yaml must be reachable through the API.
    A hard-coded impl that only knows R_75 would fail when YAML adds a new pair.
    """
    checked = 0
    for inst_id, broker, native in _iter_yaml_mappings():
        assert reference.exists(inst_id), f"YAML instrument {inst_id!r} missing from master"
        assert reference.to_native(inst_id, broker) == native
        assert reference.to_canonical(native, broker) == inst_id
        checked += 1
    assert checked >= 7, f"YAML source-of-truth check only covered {checked} pairs"
