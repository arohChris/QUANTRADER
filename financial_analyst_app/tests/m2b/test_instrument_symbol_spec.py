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
  5) Review F2/F4/F5/F7 2026-09-22: named GBPUSD+BTCUSD/mt5 gates; to_native unlisted-broker +
     unknown-id pins; exists() unknown-broker False; get_instrument deepcopy isolation.

Gate: to_canonical("frxEURUSD","deriv") == "FX.EURUSD"
Scope 2026-09-22: forex|crypto|commodity only — synthetic removed from product.
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
_YAML_FILES = ("forex.yaml", "crypto.yaml", "commodities.yaml")


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

def test_to_canonical_frxEURUSD_deriv_gate() -> None:
    # P05 gate (scope-corrected 2026-09-22): frxEURUSD -> FX.EURUSD
    assert reference.to_canonical("frxEURUSD", "deriv") == "FX.EURUSD"


def test_to_canonical_EURUSD_mt5() -> None:
    assert reference.to_canonical("EURUSD", "mt5") == "FX.EURUSD"


def test_to_canonical_cryBTCUSD_deriv() -> None:
    # REV1 2026-09-22: crypto instrument_id namespaced CRYPTO.* per human gate
    assert reference.to_canonical("cryBTCUSD", "deriv") == "CRYPTO.BTCUSD"


def test_to_native_roundtrip_I02_forex_deriv() -> None:
    assert reference.to_native("FX.EURUSD", "deriv") == "frxEURUSD"
    assert reference.to_canonical("frxEURUSD", "deriv") == "FX.EURUSD"


def test_unknown_native_passthrough() -> None:
    # Spec edge: unknown native returns unchanged (M2.3 validates later)
    assert reference.to_canonical("NOT_A_SYMBOL", "deriv") == "NOT_A_SYMBOL"


def test_unknown_broker_raises_typed_error() -> None:
    # Review #1: exact type, not bare Exception
    with pytest.raises(UnknownBrokerError) as exc:
        reference.to_canonical("frxEURUSD", "coinbase")
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
    # headway not in commodities.yaml brokers for XAUUSD
    assert reference.exists("COMMODITY.XAUUSD", broker="headway") is False


def test_exists_without_broker() -> None:
    assert reference.exists("FX.EURUSD") is True
    assert reference.exists("NOPE.MISSING") is False


def test_exists_unknown_broker_string_returns_false() -> None:
    # F5 (review 2026-09-22): exists() with unknown broker → False (not raise);
    # to_canonical unknown broker still raises (spec:33) — asymmetry pinned.
    assert reference.exists("FX.EURUSD", broker="coinbase") is False
    assert reference.exists("FX.EURUSD", broker="") is False
    assert reference.exists("FX.EURUSD", broker=None) is True


def test_to_native_known_id_unlisted_broker_raises_reference_validation() -> None:
    # F4 (review 2026-09-22): known id + broker with no mapping → ReferenceValidationError
    # (distinct from UnknownInstrumentError for id missing entirely — pinned per review).
    with pytest.raises(ReferenceValidationError) as exc:
        reference.to_native("FX.GBPUSD", "headway")
    assert "FX.GBPUSD" in str(exc.value)
    assert "headway" in str(exc.value)


def test_to_native_unknown_id_raises_unknown_instrument() -> None:
    # F4: unknown id → UnknownInstrumentError (spec:34 path for reverse map)
    with pytest.raises(UnknownInstrumentError) as exc:
        reference.to_native("NOPE.MISSING", "deriv")
    assert "NOPE.MISSING" in str(exc.value)


def test_exists_broker_none_vs_listed() -> None:
    assert reference.exists("FX.GBPUSD") is True
    assert reference.exists("FX.GBPUSD", broker=None) is True
    # GBPUSD not listed for headway
    assert reference.exists("FX.GBPUSD", broker="headway") is False


def test_case_sensitive_no_silent_lower() -> None:
    # Spec: case-sensitive — frxeurusd must NOT map
    assert reference.to_canonical("frxeurusd", "deriv") == "frxeurusd"


def test_deterministic_same_input_same_output_I08() -> None:
    a = reference.to_canonical("frxEURUSD", "deriv")
    b = reference.to_canonical("frxEURUSD", "deriv")
    assert a == b == "FX.EURUSD"


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
        reference.to_canonical("frxEURUSD", "")
    with pytest.raises(UnknownBrokerError):
        reference.to_canonical("frxEURUSD", "")  # empty broker → typed broker error (spec edge)


def test_adversarial_mapped_id_not_in_master_detectable() -> None:
    # If a mapping pointed at unknown id, exists() must be False (detectable; P06 validates at publish)
    assert reference.exists("FX.EURUSD") is True
    assert reference.exists("FX.GHOST") is False


def test_no_user_id_in_cache_key_I06() -> None:
    # Shared truth: same result regardless of any tenant — API takes no user_id
    import inspect
    sig = inspect.signature(reference.to_canonical)
    assert "user_id" not in sig.parameters
    assert "user_id" not in inspect.signature(reference.get_instrument).parameters


def test_get_instrument_returns_deepcopy_isolation() -> None:
    # F7 (review 2026-09-22): caller mutation must not corrupt shared _MASTER
    a = reference.get_instrument("FX.EURUSD")
    a["canonical_symbol"] = "HACKED"
    a["brokers"] = []
    b = reference.get_instrument("FX.EURUSD")
    assert b["canonical_symbol"] == "EURUSD"
    assert "deriv" in b["brokers"]
    assert reference.to_canonical("frxEURUSD", "deriv") == "FX.EURUSD"


# --- Commodity (asset class expansion — defaults 2026-09-22) ---

def test_to_canonical_frxGBPUSD_deriv_gate() -> None:
    # F2 (review 2026-09-22): named test for spec:46 gate pair
    assert reference.to_canonical("frxGBPUSD", "deriv") == "FX.GBPUSD"


def test_to_canonical_BTCUSD_mt5_gate() -> None:
    # F2 (review 2026-09-22): named test for spec:48 gate pair (mt5 native == display)
    assert reference.to_canonical("BTCUSD", "mt5") == "CRYPTO.BTCUSD"


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
    # Scope guard: no synthetic/index/equity instruments in master after 2026-09-22 cut
    for inst_id, _broker, _native in mappings:
        assert not inst_id.startswith(("SYNTHETIC.", "INDEX.", "EQUITY.")), inst_id
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
