"""
Tick size must equal the BROKER's `pip_size`. Found 2026-09-27 by reading Deriv's
`active_symbols` (Auth: not required) — our config was wrong for 2 of 5 assets.

  BTCUSD: config 0.01   vs broker 0.001   -> 10x too coarse
  XAGUSD: config 0.01   vs broker 0.0001  -> 100x too coarse (two orders of magnitude)

WHY this matters more than it looks: tick_size is the quantum every price is rounded to
(`normalize.py: _round_to_tick`). A 100x-coarse tick on Silver does not add noise, it
DISCARDS two decimal orders of magnitude of the real price. Every downstream indicator, gap
and backtest inherits the error, and nothing downstream can detect it.

The source of truth is the committed REAL broker response in
`tests/fixtures/deriv/active_symbols_brief.json`, so this test is offline and deterministic.
The LIVE check is a separate boot-time guard, `scripts/verify_broker_reference.py`, which
fails the process on a mismatch rather than merely warning.

Written BEFORE the fix - the two wrong values are observed RED, not assumed.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from modules.reference.reference import get_instrument, to_native

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "deriv" / "active_symbols_brief.json"
ASSETS = Path(__file__).resolve().parents[2] / "config" / "assets"


def _broker_pip_sizes() -> dict[str, Decimal]:
    """instrument_id -> authoritative tick size, parsed EXACTLY from the real response."""
    raw = FIXTURE.read_text(encoding="utf-8")
    frame = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
    out: dict[str, Decimal] = {}
    for entry in frame["active_symbols"]:
        native = entry["underlying_symbol"]
        pip = entry.get("pip_size")
        if pip is None:
            continue
        # pip_size arrives as a JSON number; Decimal keeps the literal exact.
        out[native] = pip if isinstance(pip, Decimal) else Decimal(str(pip))
    return out


BROKER = _broker_pip_sizes()
SHIPPED = ["CRYPTO.BTCUSD", "FX.EURUSD", "FX.GBPUSD", "COMMODITY.XAUUSD", "COMMODITY.XAGUSD"]


@pytest.mark.parametrize("instrument_id", SHIPPED)
def test_tick_size_equals_broker_pip_size(instrument_id: str) -> None:
    native = to_native(instrument_id, "deriv")
    assert native in BROKER, f"{instrument_id} ({native}) not in the real broker response"
    expected = BROKER[native]
    actual = get_instrument(instrument_id)["tick_size"]
    assert actual == expected, (
        f"{instrument_id}: tick_size {actual} != broker pip_size {expected} - prices are "
        f"rounded to this quantum, so a wrong tick silently corrupts every price"
    )


def test_the_two_known_defects_are_named_so_a_regression_is_visible() -> None:
    """Pin the exact values that were wrong, so nobody 'fixes' them back."""
    assert get_instrument("CRYPTO.BTCUSD")["tick_size"] == Decimal("0.001")
    assert get_instrument("COMMODITY.XAGUSD")["tick_size"] == Decimal("0.0001")


def test_no_shipped_instrument_still_carries_a_coarse_default() -> None:
    """Guards the whole class of error: an asset inheriting a class default that is coarser
    than the broker's tick. Silver is the case that motivated this."""
    coarse = []
    for instrument_id in SHIPPED:
        native = to_native(instrument_id, "deriv")
        ours = get_instrument(instrument_id)["tick_size"]
        theirs = BROKER.get(native)
        if theirs is not None and ours > theirs:
            coarse.append(f"{instrument_id}: {ours} > broker {theirs}")
    assert coarse == [], f"tick_size coarser than the broker's: {coarse}"


def test_authored_tick_sizes_are_quoted_strings_on_disk() -> None:
    """The money law reaches the FILE too: an unquoted number is a float (AGENTS.md 3.1)."""
    offenders: list[str] = []
    for path in sorted(ASSETS.glob("*.yaml")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("tick_size:"):
                value = line.split(":", 1)[1].strip()
                if not value.startswith(('"', "'")):
                    offenders.append(f"{path.name}: tick_size: {value}")
    assert offenders == [], f"unquoted money in config: {offenders}"


def test_every_shipped_instrument_appears_in_the_real_broker_response() -> None:
    """If an instrument is not in the broker's active list we are trading a fiction."""
    missing = [
        i for i in SHIPPED
        if to_native(i, "deriv") not in BROKER
    ]
    assert missing == [], f"shipped but absent from active_symbols: {missing}"


def test_config_remains_loadable_and_unchanged_in_shape() -> None:
    """The fix is a value correction, not a structural change - prove the files still parse
    into the shape the loader expects."""
    for path in sorted(ASSETS.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert isinstance(data, dict), path.name
        for key, value in data.items():
            if isinstance(value, dict) and "asset_class" in value:
                assert "tick_size" in value, f"{path.name}:{key} lost tick_size"
