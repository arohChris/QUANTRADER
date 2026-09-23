"""M2.7 real-data verify — watermark walk + disjoint + three-run demos (REV2.1).

Usage (from financial_analyst_app): python scripts/verify_m2.7_recovery.py
Path lock: scripts/ → _ROOT parents[1] → from modules.m2_market_data... (same as M2.6).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.m2_market_data.recovery import GapAnalyzer, RecoveryEngine  # noqa: E402, path lock


async def main() -> int:
    ga = GapAnalyzer()
    print("wm\tmissing")
    missing: list[int] = []
    for wm in (1, 2, 5):
        missing = ga.observe("FX.EURUSD", "deriv", wm)
        print(f"{wm}\t{missing}")
    runs = ga.to_intervals(missing)
    print(f"runs\t{runs}")

    def choose(instrument_id: str, broker: str) -> str:
        print(f"choose\t{instrument_id}\t{broker}")
        return "fake_rest"

    async def fetch(chunk, source):
        print(f"fetch\t{chunk}\t{source}")
        return [f"backfill_{chunk[0]}_{chunk[1]}"]

    async def write(data, instrument_id, broker, chunk):
        print(f"write\t{chunk}\t{data}")

    eng = RecoveryEngine(choose_source=choose, fetch=fetch, write_backfill=write, chunk_size=10)
    result = await eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=missing)
    print(f"result\t{result.status}\tchunks={result.chunks_done}/{result.chunk_count}")

    # Disjoint demo: [5,7] must fetch only those points (blocker check)
    print("--- disjoint [5,7] ---")
    result2 = await eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[5, 7])
    print(f"disjoint\t{result2.status}\tmissing_count={result2.missing_count}\tchunks={result2.chunk_count}")

    # §9 three-run: [5,6,10,11,12,20] → three chunks only
    print("--- three-run [5,6,10,11,12,20] ---")
    runs3 = ga.to_intervals([5, 6, 10, 11, 12, 20])
    print(f"runs3\t{runs3}")
    result3 = await eng.on_gap(
        "FX.EURUSD", "deriv", missing_watermarks=[5, 6, 10, 11, 12, 20]
    )
    print(
        f"three\t{result3.status}\tmissing={result3.missing_count}\t"
        f"chunks={result3.chunk_count}\tdone={result3.chunks_done}"
    )

    ok = (
        result.status == "ok"
        and missing == [3, 4]
        and result2.status == "ok"
        and result2.missing_count == 2
        and result2.chunk_count == 2
        and runs3 == [(5, 6), (10, 12), (20, 20)]
        and result3.status == "ok"
        and result3.chunk_count == 3
        and result3.chunks_done == 3
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
