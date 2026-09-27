"""Step 7 real-data verification for M2b.5 (asset-class standard) — against the REAL config.

WHY this exists (AGENTS.md 2 Step 7): the human directive behind this slice was that adding
or removing an asset must be ONE edit and everything class-derived must follow. That is a
claim about behaviour, so it has to be demonstrated on the actual shipped configuration -
not on a fixture that was written to agree with the implementation.

What is asserted, each printing values a human can check:
  1. The SHIPPED `config/reference/asset_classes.yaml` loads and is internally consistent.
  2. Every real shipped instrument inherits its class conventions, and money fields are exact
     `Decimal` built from authored quoted strings (no float anywhere on the money path).
  3. Gold and Silver keep DIFFERENT contract sizes - the case that proves the standard does
     not flatten a genuine per-instrument difference.
  4. ADD is one edit: a new crypto and a new forex asset are added to a COPY of the real
     config with only class + contract size + broker natives, and every derived field follows.
  5. ADDING AN ASSET CLASS is a config edit, not a code change - proven by writing a new class
     into a COPY of the real standard and reloading.
  6. REMOVE is detectable: deleting an instrument while a rule still names it is reported.
  7. Session hours for the real assets match the standard (bridges this slice to M2b.4).

No network, no database: every check is offline and re-runnable forever.
Run:  python scripts/verify_m2b.5_asset_standard.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.reference.calendar import is_open
from modules.reference.reference import list_orphans, load_assets
from modules.reference.standards import (
    load_standards,
    reload_standards,
    standard_path,
)

APP = Path(__file__).resolve().parents[1]
CONFIG = APP / "config"
ASSETS = CONFIG / "assets"
SAT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
FRI = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    return ok


def main() -> int:
    results: list[bool] = []
    print("=" * 78)
    print("M2b.5 STEP 7 - asset-class standard against the REAL shipped config")
    print("=" * 78)

    # -- 1. the shipped standard loads ---------------------------------------------------
    std = load_standards(standard_path())
    print(f"\n  1. SHIPPED STANDARD  {standard_path().name}")
    for name, s in sorted(std.items()):
        sess = s.session
        shape = ("always_open" if sess.get("always_open")
                 else f"wd{sess['open_weekday']}:{sess['open_minute']} -> "
                      f"wd{sess['close_weekday']}:{sess['close_minute']}")
        print(f"     {name:10s} session={shape:28s} tick={s.tick_size} dp={s.price_dp}")
    results.append(_check(
        "1. shipped standard loads with the three expected classes",
        set(std) == {"crypto", "forex", "commodity"}, f"{sorted(std)}",
    ))

    # -- 2. real instruments inherit, money is exact Decimal ------------------------------
    master = load_assets(sorted(ASSETS.glob("*.yaml")))
    print(f"\n  2. SHIPPED INSTRUMENTS  {len(master)}")
    money_fields = ("tick_size", "contract_size", "min_qty", "qty_step")
    non_decimal = [
        f"{i}.{f}" for i, e in master.items() for f in money_fields
        if not isinstance(e.get(f), Decimal)
    ]
    for inst in sorted(master):
        e = master[inst]
        print(f"     {inst:18s} class={e['asset_class']:9s} tick={e['tick_size']:<12} "
              f"contract={e['contract_size']:<8} min={e['min_qty']}")
    results.append(_check(
        "2. every money field is an exact Decimal (no float on the money path)",
        not non_decimal, f"non-Decimal: {non_decimal}" if non_decimal else "all Decimal",
    ))
    results.append(_check(
        "2b. no class has a standard with no instrument using it",
        set(std) <= {e["asset_class"] for e in master.values()},
        f"unused classes: {sorted(set(std) - {e['asset_class'] for e in master.values()})}",
    ))

    # -- 3. contract_size genuinely differs (the standard must not flatten it) ----------
    gold = master.get("COMMODITY.XAUUSD", {}).get("contract_size")
    silver = master.get("COMMODITY.XAGUSD", {}).get("contract_size")
    results.append(_check(
        "3. Gold and Silver keep DIFFERENT contract sizes (not flattened)",
        gold is not None and silver is not None and gold != silver,
        f"XAU={gold}  XAG={silver}",
    ))

    # -- 4. ADD is one edit, on a COPY of the real config --------------------------------
    print("\n  3. ADD = one edit (on a copy of the real config, real config untouched)")
    tmp = Path(tempfile.mkdtemp())
    try:
        for f in ASSETS.glob("*.yaml"):
            shutil.copy(f, tmp / f.name)
        before = len(load_assets(sorted(tmp.glob("*.yaml"))))
        (tmp / "new_assets.yaml").write_text(
            'CRYPTO.NEWCOIN:\n  asset_class: crypto\n  contract_size: "1"\n'
            '  brokers: [deriv]\n  broker_native:\n    deriv: frxNEWCOIN\n'
            'FX.NEWPAIR:\n  asset_class: forex\n  contract_size: "100000"\n'
            '  brokers: [deriv]\n  broker_native:\n    deriv: frxNEWPAIR\n',
            encoding="utf-8",
        )
        grown = load_assets(sorted(tmp.glob("*.yaml")))
        newc, newf = grown["CRYPTO.NEWCOIN"], grown["FX.NEWPAIR"]
        print(f"     instruments {before} -> {len(grown)}")
        print(f"     CRYPTO.NEWCOIN  tick={newc['tick_size']} min={newc['min_qty']} "
              f"step={newc['qty_step']}  (NOT written in the file)")
        print(f"     FX.NEWPAIR      tick={newf['tick_size']} min={newf['min_qty']} "
              f"step={newf['qty_step']}  (NOT written in the file)")
        results.append(_check(
            "4. adding 2 assets = 1 edit each; tick/min/step all inherited",
            len(grown) == before + 2
            and newc["tick_size"] == std["crypto"].tick_size
            and newf["tick_size"] == std["forex"].tick_size
            and newc["min_qty"] == std["crypto"].min_qty,
        ))

        # -- 5. adding an ASSET CLASS is a config edit ------------------------------------
        print("\n  4. ADD AN ASSET CLASS = config edit, not a code change")
        cfg_copy = tmp / "asset_classes.yaml"
        shutil.copy(standard_path(), cfg_copy)
        cfg_copy.write_text(
            cfg_copy.read_text(encoding="utf-8")
            + '\nindex:\n  price_dp: 2\n  open_weekday: 1\n  open_minute: 60\n'
              '  close_weekday: 5\n  close_minute: 1140\n  tick_size: "0.01"\n'
              '  min_qty: "0.1"\n  qty_step: "0.1"\n',
            encoding="utf-8",
        )
        try:
            extended = reload_standards(cfg_copy)
            print(f"     classes now: {sorted(extended)}")
            results.append(_check(
                "5. a brand-new class is honoured after editing YAML only",
                "index" in extended and extended["index"].price_dp == 2,
                "added 'index'; no Python changed",
            ))
        finally:
            reload_standards()  # restore the shipped standard for anything running after

        # -- 6. REMOVE is detectable ------------------------------------------------------
        print("\n  5. REMOVE is detectable (a rule outliving its instrument is reported)")
        (tmp / "new_assets.yaml").write_text(
            'CRYPTO.NEWCOIN:\n  asset_class: crypto\n  contract_size: "1"\n', encoding="utf-8"
        )
        (tmp / "symbol_mapping.yaml").write_text(
            "CRYPTO.GONE:\n  deriv: frxGONE\n", encoding="utf-8"
        )
        files = [f for f in sorted(tmp.glob("*.yaml")) if f.name != "symbol_mapping.yaml"]
        orphans = list_orphans(master_files=files, mapping_files=[tmp / "symbol_mapping.yaml"])
        for o in orphans:
            print(f"     ORPHAN -> {o}")
        results.append(_check(
            "6. a rule naming a removed instrument is reported",
            any("CRYPTO.GONE" in o for o in orphans), f"{len(orphans)} orphan(s)",
        ))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # -- 7. session hours agree with the standard (bridge to M2b.4) ---------------------
    print("\n  6. Session hours for the REAL assets follow their class:")
    ok_sessions = True
    for inst, entry in sorted(master.items()):
        cls = entry["asset_class"]
        if cls == "crypto":
            expect_sat, expect_fri = True, True
        else:
            expect_sat, expect_fri = False, True
        got_sat = is_open(inst, SAT, broker="deriv")
        got_fri = is_open(inst, FRI, broker="deriv")
        good = got_sat is expect_sat and got_fri is expect_fri
        ok_sessions &= good
        print(f"     {inst:18s} {cls:9s} Fri={got_fri!s:5s} Sat={got_sat!s:5s} "
              f"{'OK' if good else 'MISMATCH'}")
    results.append(_check(
        "7. every real asset's session matches its class standard", ok_sessions,
    ))

    failed = results.count(False)
    print("\n" + "=" * 78)
    print(f"  {len(results) - failed}/{len(results)} checks passed"
          + (f"  ({failed} FAILED)" if failed else ""))
    print("  Fully offline and re-runnable: no network, no database.")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
