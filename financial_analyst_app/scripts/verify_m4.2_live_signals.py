#!/usr/bin/env python3
"""WHY: M4.2 Live Signals Console — human directive ACTION 57 (2026-09-26): open the
Deriv WebSocket, collect real data, and show M3 analysis + M4.2 strategy verdicts
running, dry-run only (no orders — G0 / AGENTS §3.10). Step-7-style live
re-verification of the COMPLETE m4.2 / m3.2 / m2.x chain via public APIs only —
no new calculations, no Signal contract (M4.1 stays parked at A55).

Run:     python scripts/verify_m4.2_live_signals.py
         [--symbol frxEURUSD] [--history-candles 200] [--duration-s 0]
         [--out-dir data/live_signals]
Spec:    docs/specs/m4.2_live_signals.spec.md (helpers + edges + exit codes)
Covers:  real ticks_history seed (schema-validated -> frozen Candle) ->
         seed eval immediately -> live ticks -> M2.1->M2.3->M2.4 pipeline ->
         finalized 1m candle -> FeatureEngine (P15 capability interlock) ->
         eligible M4.2 evaluate() verdict rows; jsonl collection artifacts.
Exit:    0 = ran with >=1 eval / 1 = empty-or-parse fail / 2 = connect-or-API fail /
         3 = market closed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from modules.m2_market_data import capture
from modules.m2_market_data.candles import CandleBuilder
from modules.m2_market_data.ingest.ingest import TickIngestor
from modules.m2_market_data.normalize import Normalizer
from modules.m2_market_data.validate import validate
from modules.m2_market_data.watermark import WatermarkRegistry
from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.feature_engine import FeatureEngine, FeatureRequest
from modules.m3_analytics.engine.profiles import Profiles
from modules.m3_analytics.strategies import (
    donchian_breakout,
    mean_reversion,
    microstructure_flow,
    pullback,
    regime_based,
    rsi_overbought_oversold,
    trend_following,
)
from modules.reference import reference
from shared.contracts.drop import Drop
from shared.contracts.internal_tick import InternalTick
from shared.contracts.market_data import Candle, MarketData
from shared.core.clock import SystemClock

EXIT_OK, EXIT_FAIL, EXIT_CONNECT, EXIT_CLOSED = 0, 1, 2, 3
_CAP_YAML = _ROOT / "config" / "capabilities.yaml"
_ASSETS_DIR = _ROOT / "config" / "assets"
_GRANULARITY_S = 60  # Deriv history granularity (seconds) == our built 1m candle
_MAX_RECONNECTS = 10  # WHY: bounded ladder — a dead network must exit 2, not spin
_RNG = random.Random()  # jitter source for reconnect_delay (tests pass seeded rng)

# M4.2 public contract only: NAME / FEATURE_SLOTS / PARAM_DEFAULTS / evaluate
MODULES = (
    trend_following,
    rsi_overbought_oversold,
    mean_reversion,
    donchian_breakout,
    pullback,
    regime_based,
    microstructure_flow,
)


class HistoryResponseError(ValueError):
    """Deriv ticks_history response failed schema/seed rules (spec §helper 1)."""


class FeatureUnionError(ValueError):
    """Strategy module missing the FEATURE_SLOTS public contract."""


# --- pure helpers (unit-tested — the only network-free surface) -----------------


def history_candles_from_response(msg: object, *, timeframe: str = "1m") -> list[Candle]:
    """Deriv style=candles response -> frozen Candle list, sorted, invalid rows skipped."""
    if not isinstance(msg, dict):
        raise HistoryResponseError(f"response must be a dict, got {type(msg).__name__}")
    rows = msg.get("candles")
    if not isinstance(rows, list):
        raise HistoryResponseError("response missing a list under 'candles'")
    if not rows:
        raise HistoryResponseError("empty candles seed — never passes")
    if timeframe != "1m":
        raise HistoryResponseError(f"only 1m seed supported, got {timeframe!r}")
    candles: list[Candle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        epoch = row.get("epoch")
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            continue  # bool is an int subclass — adversarial rows must not slip in
        try:
            open_ = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(map(_finite, (open_, high, low, close))):
            continue
        start = datetime.fromtimestamp(epoch, tz=UTC)
        volume = row.get("volume")
        candles.append(
            Candle(
                open=open_,
                high=high,
                low=low,
                close=close,
                tick_count=volume
                if isinstance(volume, int) and not isinstance(volume, bool)
                else 0,
                interval=timeframe,
                interval_start=start,
                interval_end=start + timedelta(seconds=_GRANULARITY_S),
            )
        )
    if not candles:
        raise HistoryResponseError("all candle rows invalid — seed unusable")
    candles.sort(key=lambda c: c.interval_start)
    return candles


def _finite(value: float) -> bool:
    return not math.isnan(value) and not math.isinf(value)


def feature_union(modules: tuple) -> tuple[str, ...]:
    """Dedup, first-seen order, over every slot's (analytic, overrides) tuple."""
    seen: list[str] = []
    for module in modules:
        slots = getattr(module, "FEATURE_SLOTS", None)
        if not isinstance(slots, dict):
            name = getattr(module, "NAME", type(module).__name__)
            raise FeatureUnionError(f"module {name!r} lacks FEATURE_SLOTS contract")
        for spec in slots.values():
            analytic = spec[0]
            if analytic not in seen:
                seen.append(analytic)
    return tuple(seen)


def slot_features(module, analytics: dict | None) -> tuple[dict[str, float], list[str]]:
    """slot <- analytics[analytic]; absent/None -> 'slot=analytic' in missing (fail-safe)."""
    mapped: dict[str, float] = {}
    missing: list[str] = []
    for slot, spec in module.FEATURE_SLOTS.items():
        analytic = spec[0]
        value = (analytics or {}).get(analytic)
        if value is None:
            missing.append(f"{slot}={analytic}")
        else:
            mapped[slot] = float(value)
    return mapped, missing


def verdict(value: float) -> tuple[str, str]:
    """Sign mapping (m4.2 contract) + honest out-of-range flag, never clamped."""
    direction = "LONG" if value > 0 else "SHORT" if value < 0 else "NONE"
    note = str(value)
    if abs(value) > 1.0:
        note += " out-of-range"
    return direction, note


def slot_overrides(modules: tuple) -> dict[str, dict[str, float]]:
    """Merge every slot's pin (e.g. ema_20@10) per ANALYTIC for FeatureRequest."""
    merged: dict[str, dict[str, float]] = {}
    for module in modules:
        for analytic, overrides in module.FEATURE_SLOTS.values():
            merged.setdefault(analytic, {}).update(overrides)
    return merged


def append_series(series: list[Candle], candle: Candle) -> None:
    """WHY replace-or-append: Deriv's end=latest seed includes the FORMING candle;
    when the live pipeline finalizes that same bucket, appending would duplicate
    interval_start and corrupt the engine window — the finalized bar wins."""
    for i, existing in enumerate(series):
        if existing.interval_start == candle.interval_start:
            series[i] = candle
            return
    series.append(candle)


def candle_row(c: Candle) -> dict[str, object]:
    """WHY ONE serializer for every candle jsonl row (spec helper 7, amended
    2026-09-26 ACTION 62): the live writer hand-rolled a 4-key dict (missing
    interval_end/high/low) while the seed writer emitted 7 — every live bar was a
    partial the m4.6 reader had to skip+count. One shape, seed == live."""
    return {
        "interval_start": c.interval_start.isoformat(),
        "interval_end": c.interval_end.isoformat(),
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "tick_count": c.tick_count,
    }


def reconnect_delay(
    attempt: int, rng: random.Random, *, base: float = 1.0, cap: float = 30.0
) -> float:
    """WHY (spec helper 6, amended 2026-09-26 after live crash): jittered exponential
    backoff so a dropped Deriv stream reconnects politely — Deriv killed a healthy
    8-minute collector with `keepalive ping timeout`; retry-storming the broker is
    as bad as giving up. Exponent clamped at 60 so huge attempts hit `cap` exactly
    instead of OverflowError."""
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    step = base * 2.0 ** min(attempt - 1, 60)  # clamp BEFORE pow — adversarial 10**6
    jittered = step * (0.5 + rng.random())  # uniform [0.5*step, 1.5*step)
    return float(min(cap, jittered))  # hard cap survives any jitter


class _SeriesProvider:
    """CandleProvider over the live-growing series — filter semantics identical to
    feature_engine.StaticCandleProvider (window_start <= start, end <= window_end)."""

    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles  # shared by reference: grows as live candles close

    async def get_candles(
        self,
        instrument_id: str,
        timeframe: str,
        window_start: datetime,
        window_end: datetime,
        as_of: datetime,
        broker: str,
    ) -> list[Candle]:
        del instrument_id, as_of, broker
        return [
            candle
            for candle in self._candles
            if candle.interval == timeframe
            and window_start <= candle.interval_start
            and candle.interval_end <= window_end
        ]


class _ReferenceFacade:
    """Same facade pattern as verify_m2.1/m2.11 — real config reads."""

    def current_snapshot_id(self) -> str | None:
        return reference.current_snapshot_id()

    def to_canonical(self, native: str, broker: str) -> str:
        return reference.to_canonical(native, broker)

    def exists(self, instrument_id: str, broker: str) -> bool:
        return reference.exists(instrument_id, broker)

    def get_instrument(self, instrument_id: str) -> dict:
        return reference.get_instrument(instrument_id)


def _load_endpoint() -> str:
    cfg_path = _ROOT / "config" / "brokers" / "deriv.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    ws_url, app_id = cfg.get("ws_url"), str(cfg.get("app_id") or "")
    if not ws_url or not app_id:
        raise SystemExit(f"[EXIT 2] deriv.yaml missing ws_url/app_id: {cfg_path}")
    sep = "&" if "?" in str(ws_url) else "?"
    return f"{ws_url}{sep}app_id={app_id}"


def _fetch_history(
    url: str, symbol: str, count: int, timeout_s: float, end: str = "latest"
) -> dict:
    """One-shot ticks_history (style=candles) over the same Deriv WS endpoint.

    WHY `end`: on a closed market Deriv answers end=latest with an EMPTY candle
    list; an explicit past epoch still returns the last real bars (probed 2026-09-26).
    """
    frame = json.dumps(
        {
            "ticks_history": symbol,
            "style": "candles",
            "granularity": _GRANULARITY_S,
            "count": count,
            "end": int(end) if end.isdigit() else end,
        }
    )
    ws = capture.connect_with_retry(
        lambda: connect(url, open_timeout=10), attempts=8, sleep=time.sleep
    )
    try:
        ws.send(frame)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                raw = ws.recv(timeout=max(deadline - time.monotonic(), 0.1))
            except TimeoutError:
                break
            if not isinstance(raw, str):
                continue
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            kind = capture.classify(msg)
            if kind == "error":
                err = msg.get("error") or {}
                raise SystemExit(
                    f"[EXIT 2] history API error: {err.get('code')}: "
                    f"{str(err.get('message', ''))[:200]}"
                )
            if kind == "closed":
                err = msg.get("error") or {}
                opens = (err.get("code_args") or ["unknown"])[0]
                raise SystemExit(f"[EXIT 3] MarketIsClosed for {symbol!r} — opens {opens}")
            if msg.get("msg_type") == "candles":
                return msg
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001, S110 — close is best-effort
            pass
    raise SystemExit("[EXIT 2] no ticks_history candles response within timeout")


def _print_eval(
    engine: FeatureEngine,
    eligible: tuple,
    blocked: dict[str, str],
    series: list[Candle],
    as_of: datetime,
    label: str,
    evals_fh,
    instrument_id: str,
    asset_class: Literal["forex", "crypto", "commodity"],
) -> None:
    """One eval block: analytics line + per-strategy verdict rows + jsonl record."""
    features = feature_union(eligible)
    res = asyncio.run(
        engine.get_features(
            FeatureRequest(
                instrument_id=instrument_id,
                broker="deriv",
                timeframe="1m",
                features=features,
                as_of=as_of,
                asset_class=asset_class,
                param_overrides=slot_overrides(eligible),
            )
        )
    )
    latest = series[-1]
    print(
        f"\n=== EVAL {label} as_of={as_of.isoformat()} bars={len(series)} "
        f"last=[{latest.interval_start.isoformat()} O={latest.open} C={latest.close}] ==="
    )
    analytics_line = " ".join(
        f"{name}={value:.5g}" for name, value in sorted(res.features.items())
    )
    print(f"ANALYTICS {analytics_line or '(none)'}")
    if res.capability_warning:
        print(f"capability: {res.capability_warning}")
    record: dict = {
        "as_of": as_of.isoformat(),
        "bars": len(series),
        "features": dict(res.features),
        "capability_warning": res.capability_warning,
        "strategies": {},
    }
    print(f"{'STRATEGY':<26} {'VERDICT':<8} VALUE")
    for module in eligible:
        if module.NAME in blocked:
            print(f"{module.NAME:<26} {'BLOCKED':<8} {blocked[module.NAME]}")
            record["strategies"][module.NAME] = {"verdict": "BLOCKED"}
            continue
        mapped, missing = slot_features(module, res.features)
        try:
            value = module.evaluate(mapped, dict(module.PARAM_DEFAULTS))
            direction, note = verdict(value)
        except ValueError as exc:
            direction, note, value = "ERROR", str(exc), None
        line = f"{module.NAME:<26} {direction:<8} {note}"
        if missing:
            line += f"  (missing: {', '.join(missing)})"
        print(line)
        record["strategies"][module.NAME] = {
            "verdict": direction,
            "value": value,
            "missing": missing,
        }
    evals_fh.write(
        json.dumps(record, separators=(",", ":")) + "\n"
    )
    evals_fh.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="M4.2 live signals console (Step 7)")
    parser.add_argument("--symbol", default="frxEURUSD")
    parser.add_argument("--history-candles", type=int, default=200)
    parser.add_argument("--duration-s", type=float, default=0.0, help="0 = run until interrupted")
    parser.add_argument("--history-timeout-s", type=float, default=60.0)
    parser.add_argument("--out-dir", type=Path, default=_ROOT / "data" / "live_signals")
    args = parser.parse_args()

    url = _load_endpoint()
    print(f"[info] endpoint={url.split('?')[0]} symbol={args.symbol} (app_id never printed)")

    # --- seed: real Deriv 1m history, schema-validated into frozen Candles -------
    # Retry ladder: end=latest first (fresh bars when market open); on an empty
    # seed (closed-market quirk) retry at now-86400 before giving up — spec edges.
    series: list[Candle] | None = None
    last_err: Exception | None = None
    for end in ("latest", str(int(time.time()) - 86400)):
        try:
            msg = _fetch_history(
                url, args.symbol, args.history_candles, args.history_timeout_s, end=end
            )
            series = history_candles_from_response(msg)
            break
        except HistoryResponseError as exc:
            last_err = exc
            print(f"[seed] end={end}: {exc} — {'retrying' if end == 'latest' else 'giving up'}")
        except SystemExit as exc:
            print(exc)
            code = str(exc)
            return EXIT_CLOSED if "[EXIT 3]" in code else EXIT_CONNECT
    if series is None:
        print(f"[EXIT 1] seed rejected after retries: {last_err}")
        return EXIT_FAIL
    instrument_id = reference.to_canonical(args.symbol, "deriv")
    if not reference.exists(instrument_id):
        print(f"[EXIT 1] {args.symbol!r} -> {instrument_id!r} not in instrument master")
        return EXIT_FAIL
    raw_class = reference.get_instrument(instrument_id).get("asset_class")
    if raw_class not in ("forex", "crypto", "commodity"):
        print(f"[EXIT 1] invalid asset_class {raw_class!r} for {instrument_id}")
        return EXIT_FAIL
    asset_class = cast(Literal["forex", "crypto", "commodity"], raw_class)
    print(f"[seed] {len(series)} candles {series[0].interval_start.isoformat()} -> "
          f"{series[-1].interval_end.isoformat()} instrument={instrument_id} "
          f"asset_class={asset_class}")

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out_dir / day
    out_dir.mkdir(parents=True, exist_ok=True)
    history_path = out_dir / "history_1m.jsonl"
    history_path.write_text(
        "".join(json.dumps(candle_row(c)) + "\n" for c in series),
        encoding="utf-8",
    )
    print(f"[data] {history_path}")

    # --- engine + capability gate (P15): eligible strategies only ----------------
    cap_registry = CapabilityRegistry.from_yaml(_CAP_YAML)
    eligible_list, blocked = [], {}
    for module in MODULES:
        ok, reason = cap_registry.is_strategy_eligible(module.NAME, "deriv")
        if ok:
            eligible_list.append(module)
        else:
            blocked[module.NAME] = reason
    eligible = tuple(eligible_list)
    print(f"[gate] eligible={len(eligible)} blocked={sorted(blocked)}")

    engine = FeatureEngine(
        provider=_SeriesProvider(series),
        capability_registry=cap_registry,
        resolver=None,
        cache=None,
        profiles=Profiles.from_dir(_ASSETS_DIR),
    )

    ticks_path = out_dir / "live_ticks.jsonl"
    candles_path = out_dir / "live_candles_1m.jsonl"
    drops_path = out_dir / "drops_live.jsonl"
    evals_path = out_dir / f"evals_{run_id}.jsonl"
    # WHY SystemClock (live) — a SimulatedClock frozen at startup made every tick
    # after the first second drop as timestamp_ahead (99/100 on 2026-09-26 probe).
    # SimulatedClock stays for frozen-history replays (m2.1/m2.11 batch captures).
    ingestor = TickIngestor(provider="deriv", clock=SystemClock())
    normalizer = Normalizer(
        reference=_ReferenceFacade(), watermark_registry=WatermarkRegistry()
    )
    builder = CandleBuilder(instrument_id=instrument_id, broker="deriv", interval="1m")

    # --- seed eval runs IMMEDIATELY: analysis+strategy visible without waiting ---
    evals = 0
    with evals_path.open("a", encoding="utf-8") as evals_fh:
        try:
            _print_eval(
                engine,
                eligible,
                blocked,
                series,
                max(c.interval_end for c in series),
                "seed",
                evals_fh,
                instrument_id,
                asset_class,
            )
            evals += 1
        except Exception as exc:  # noqa: BLE001 — seed eval failure is a hard fail
            print(f"[EXIT 1] seed eval failed: {type(exc).__name__}: {exc}")
            return EXIT_FAIL

        # --- live phase: WS ticks -> pipeline -> candle close -> eval ------------
        accepted = dropped = 0
        print(f"[live] subscribing {args.symbol} "
              f"duration={'forever' if args.duration_s <= 0 else f'{args.duration_s:.0f}s'} "
              f"-> {ticks_path}")
        attempts = 0  # consecutive connection failures; reset on any received frame
        try:
            ws = capture.connect_with_retry(
                lambda: connect(url, open_timeout=10, ping_timeout=60),
                attempts=8,
                sleep=time.sleep,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[EXIT 2] live connect failed: {type(exc).__name__}: {exc}")
            return EXIT_CONNECT
        try:
            ws.send(capture.build_capture_frame(args.symbol))
            deadline = (
                time.monotonic() + args.duration_s if args.duration_s > 0 else None
            )
            with ticks_path.open("a", encoding="utf-8") as ticks_fh, candles_path.open(
                "a", encoding="utf-8"
            ) as candles_fh, drops_path.open("a", encoding="utf-8") as drops_fh:
                while deadline is None or time.monotonic() < deadline:
                    if ws is None:
                        # Reconnect path (spec amended 2026-09-26): stream dropped,
                        # backoff already slept — bounded by _MAX_RECONNECTS so a
                        # dead network exits 2 instead of spinning forever.
                        try:
                            ws = capture.connect_with_retry(
                                lambda: connect(url, open_timeout=10, ping_timeout=60),
                                attempts=8,
                                sleep=time.sleep,
                            )
                            ws.send(capture.build_capture_frame(args.symbol))
                        except Exception as exc:  # noqa: BLE001 — bounded ladder
                            attempts += 1
                            if attempts > _MAX_RECONNECTS:
                                print(
                                    f"[EXIT 2] reconnect failed {attempts} times: "
                                    f"{type(exc).__name__}: {exc}"
                                )
                                return EXIT_CONNECT
                            print(
                                f"[reconnect] attempt={attempts} failed "
                                f"{type(exc).__name__}: {exc}"
                            )
                            time.sleep(reconnect_delay(attempts, _RNG))
                            ws = None
                        continue
                    timeout = 30.0 if deadline is None else max(
                        min(deadline - time.monotonic(), 30.0), 0.05
                    )
                    try:
                        raw = ws.recv(timeout=timeout)
                    except TimeoutError:
                        continue
                    except (ConnectionClosed, OSError) as exc:
                        # WHY: Deriv killed a healthy stream once with
                        # `keepalive ping timeout` (1011) — never fatal, count it,
                        # back off, and take the ws is None reconnect path above.
                        attempts += 1
                        if attempts > _MAX_RECONNECTS:
                            print(
                                f"[EXIT 2] connection lost {attempts} times: "
                                f"{type(exc).__name__}: {exc}"
                            )
                            return EXIT_CONNECT
                        print(
                            f"[reconnect] attempt={attempts} "
                            f"{type(exc).__name__}: {str(exc)[:120]}"
                        )
                        try:
                            ws.close()
                        except Exception:  # noqa: BLE001, S110 — best-effort close
                            pass
                        ws = None
                        time.sleep(reconnect_delay(attempts, _RNG))
                        continue
                    attempts = 0  # WHY: received frame proves the stream healthy
                    if not isinstance(raw, str):
                        continue
                    ticks_fh.write(capture.to_jsonl_line({"raw": raw}) + "\n")
                    ticks_fh.flush()  # WHY every tick: ~1Hz — buffered8KB would sit
                    # in memory on a crash and hide collection progress in logs
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    kind = capture.classify(msg)
                    if kind == "closed":
                        err = msg.get("error") or {}
                        opens = (err.get("code_args") or ["unknown"])[0]
                        print(f"[EXIT 3] market closed — opens {opens}")
                        return EXIT_CLOSED
                    if kind == "error":
                        err = msg.get("error") or {}
                        print(f"[EXIT 2] live API error: {err.get('code')}")
                        return EXIT_CONNECT
                    if kind != "tick":
                        continue
                    ingested = ingestor.ingest(raw)
                    if not isinstance(ingested, InternalTick):
                        dropped += 1
                        drops_fh.write(
                            capture.to_jsonl_line(
                                {"reason": getattr(ingested, "reason", "ingest_rejected"),
                                 "detail": repr(ingested)[:160]}
                            )
                            + "\n"
                        )
                        continue
                    market = normalizer.normalize(ingested)
                    checked = (
                        validate(market) if isinstance(market, MarketData) else market
                    )
                    if not isinstance(checked, MarketData):
                        dropped += 1
                        drops_fh.write(
                            capture.to_jsonl_line(
                                {"reason": str(getattr(checked, "reason", "invalid")),
                                 "detail": str(getattr(checked, "detail", ""))[:160]}
                            )
                            + "\n"
                        )
                        continue
                    accepted += 1
                    closed = builder.on_tick(checked)
                    if isinstance(closed, Drop):
                        dropped += 1
                        drops_fh.write(
                            capture.to_jsonl_line(
                                {"reason": str(closed.reason), "detail": closed.detail[:160]}
                            )
                            + "\n"
                        )
                        continue
                    if isinstance(closed, Candle):
                        append_series(series, closed)
                        candles_fh.write(json.dumps(candle_row(closed)) + "\n")
                        candles_fh.flush()
                        try:
                            _print_eval(
                                engine,
                                eligible,
                                blocked,
                                series,
                                closed.interval_end,
                                f"bar {closed.interval_start.isoformat()}",
                                evals_fh,
                                instrument_id,
                                asset_class,
                            )
                            evals += 1
                        except Exception as exc:  # noqa: BLE001 — keep collecting
                            print(f"[warn] eval failed: {type(exc).__name__}: {exc}")
                    if accepted % 50 == 0 and accepted:
                        print(
                            f"[live] ticks accepted={accepted} dropped={dropped} "
                            f"candles={len(series)} evals={evals}"
                        )
                # WHY inside the with-block: the trailing partial bucket must be
                # finalized, appended, and EVALUATED before evals_fh closes —
                # previously the flush happened in finally after the file was gone.
                final = builder.close()
                if isinstance(final, Candle):
                    append_series(series, final)
                    candles_fh.write(json.dumps(candle_row(final)) + "\n")
                    candles_fh.flush()
                    print(f"[live] final candle flushed {final.interval_start.isoformat()}")
                    try:
                        _print_eval(
                            engine,
                            eligible,
                            blocked,
                            series,
                            final.interval_end,
                            f"final flush {final.interval_start.isoformat()}",
                            evals_fh,
                            instrument_id,
                            asset_class,
                        )
                        evals += 1
                    except Exception as exc:  # noqa: BLE001 — run still passes on warn
                        print(f"[warn] final eval failed: {type(exc).__name__}: {exc}")
        finally:
            try:
                if ws is not None:
                    ws.close()
            except Exception:  # noqa: BLE001, S110 — best-effort close
                pass

    print(
        f"[done] accepted={accepted} dropped={dropped} candles={len(series)} "
        f"evals={evals} evals_jsonl={evals_path}"
    )
    if evals < 1:
        print("[EXIT 1] no eval produced")
        return EXIT_FAIL
    print(f"[PASS] live console ran {evals} eval(s) — dry-run only, no orders sent")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
