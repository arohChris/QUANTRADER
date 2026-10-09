"""M10.4.10 — timeframe dropdown + full-registry vocabulary — WRITTEN BEFORE CODE.

Spec: frontend/specs/m10.4.10_timeframe_dropdown.spec.md (human queue: m3.2 §10 Q5,
human directive 2026-09-30 "include all timeframes").

WHY the cross-tree read is legal: precedent test_frontend_architecture_locks.py already
reads financial_analyst_app/ from the frontend suite (_REPO = parents[2]). App code still
imports zero backend — only this test reads the registry source, which IS the parity lock.
"""
from __future__ import annotations

import re
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parents[1]
_REPO = _FRONTEND.parent
_APP = _FRONTEND / "app"
_REGISTRY_PY = _REPO / "financial_analyst_app" / "shared" / "timeframes.py"


def _backend_keys() -> list[str]:
    """Keys of INTERVAL_MINUTES from the backend registry (source of truth)."""
    text = _REGISTRY_PY.read_text(encoding="utf-8")
    block = text.split("INTERVAL_MINUTES", 1)[1].split("}", 1)[0]
    return re.findall(r'"([0-9]+[mh])"\s*:', block)


def _frontend_keys() -> list[str]:
    """Values of the frontend parity constant, in authored order (dups kept)."""
    text = (_APP / "experiment_market.ts").read_text(encoding="utf-8")
    match = re.search(r"MOCK_TIMEFRAMES\s*=\s*\[([^\]]+)\]", text)
    assert match, "MOCK_TIMEFRAMES constant missing from experiment_market.ts"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_timeframe_registry_parity_is_exact_nine() -> None:
    # Spec I1: exact key-set equality, 9 both directions, no dups.
    backend, frontend = _backend_keys(), _frontend_keys()
    assert len(backend) == 9, f"backend registry must hold 9 keys, got {backend}"
    assert len(frontend) == 9, f"frontend constant must hold 9 keys, got {frontend}"
    assert len(set(frontend)) == len(frontend), f"duplicate keys: {frontend}"
    assert set(frontend) == set(backend), (
        f"parity broken: only-in-frontend={set(frontend) - set(backend)} "
        f"only-in-backend={set(backend) - set(frontend)}"
    )


def test_trade_ticket_has_required_timeframe_field() -> None:
    # Spec Outputs: required select, label pairing, placeholder copy, one shared source.
    ticket = (_APP / "TradingTicket.tsx").read_text(encoding="utf-8")
    assert 'id="trade-timeframe"' in ticket
    assert 'htmlFor="trade-timeframe"' in ticket
    assert ">Timeframe<" in ticket
    assert "Select timeframe" in ticket
    assert "MOCK_TIMEFRAMES" in ticket, "ticket must map the shared constant (I5)"


def test_ticket_timeframe_has_no_silent_default() -> None:
    # Spec I2: state starts empty, placeholder option, nothing preselected.
    ticket = (_APP / "TradingTicket.tsx").read_text(encoding="utf-8")
    # Amendment (logged, self-caught pre-implementation): regex was written
    # `useState<?string>?` which REQUIRES the literal "string" — it could never
    # match the house style `useState("")`. Intent (empty default) unchanged.
    assert re.search(r'useState(?:<string>)?\(\s*""\s*\)', ticket), (
        "timeframe state must start empty (no silent default)"
    )
    start = ticket.index('id="trade-timeframe"')
    select_block = ticket[start : ticket.index("</select>", start)]
    assert "defaultValue" not in select_block, "timeframe select must not preselect"
    assert 'value=""' in select_block, "placeholder option (value=\"\") required"


def test_no_illegal_timeframe_literals_in_app() -> None:
    # Spec I3: the uppercase/1D vocabulary that FeatureRequest hard-rejects is gone.
    offenders: list[str] = []
    for path in sorted(_APP.rglob("*")):
        if path.suffix not in (".ts", ".tsx"):
            continue
        text = path.read_text(encoding="utf-8")
        for token in ('"1H"', '"4H"', '"1D"'):
            if token in text:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, f"illegal timeframe literals in app/: {offenders}"


def test_timeframe_window_covers_exactly_the_registry() -> None:
    # Spec E3: every registry key has a presentation window; nothing extra.
    # (tsc's Record<Timeframe, number> already forces this — second implementation.)
    text = (_APP / "experiment_market.ts").read_text(encoding="utf-8")
    block = text.split("TIMEFRAME_WINDOW", 1)[1].split("}", 1)[0]
    keys = re.findall(r'"([^"]+)"\s*:', block)
    assert len(keys) == len(set(keys)), f"duplicate window keys: {keys}"
    assert set(keys) == set(_backend_keys()), (
        f"window keys != registry: only-in-window={set(keys) - set(_backend_keys())} "
        f"only-in-registry={set(_backend_keys()) - set(keys)}"
    )


def test_no_calendar_buckets_or_malformed_keys() -> None:
    # Spec I4 / adversarial: calendar buckets are deliberately absent from the
    # backend registry (fixed-duration law) and must never enter the UI copy.
    for bad in ("1w", "1mo", "1y", "7m", "1M; rm -rf"):
        assert bad not in _frontend_keys(), f"calendar/malformed key leaked: {bad!r}"


def test_registry_constant_is_single_source_for_ticket_and_chart() -> None:
    # Spec I5: one list, two consumers — no second literal timeframe array anywhere.
    ticket = (_APP / "TradingTicket.tsx").read_text(encoding="utf-8")
    panel = (_APP / "MarketPanel.tsx").read_text(encoding="utf-8")
    assert "MOCK_TIMEFRAMES" in ticket
    assert "MOCK_TIMEFRAMES" in panel
    for path in (_APP / "TradingTicket.tsx", _APP / "MarketPanel.tsx"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r'\[\s*"1m"\s*,', text), (
            f"{path.name} must not carry its own literal timeframe list"
        )


def test_ticket_instrument_is_catalogue_select() -> None:
    # Amendment A1 (human 2026-09-30): asset chosen from the catalogue, not typed.
    ticket = (_APP / "TradingTicket.tsx").read_text(encoding="utf-8")
    assert "MOCK_INSTRUMENTS" in ticket, "options must come from the shared catalogue"
    assert '<input id="trade-instrument"' not in ticket, "instrument must not be free text"
    start = ticket.index('id="trade-instrument"')
    select_block = ticket[start : ticket.index("</select>", start)]
    assert "value={instrument}" in select_block, "controlled select (state holds the choice)"
    assert "entry.symbol" in select_block, "option value = canonical instrument id"
    assert re.search(r'useState\(\s*"FX\.EURUSD"\s*\)', ticket), (
        "default stays FX.EURUSD — the previous hardcoded value (A1)"
    )
