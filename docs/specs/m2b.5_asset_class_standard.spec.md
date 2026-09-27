# Spec: M2b.5 Asset-Class Standard — one source of truth for class-derived rules

> **Status:** DRAFT — awaiting human review. Tests written before implementation
> (`AGENTS.md` §2 Step 3).
>
> **Human directive 2026-09-27:** *"when we add or remove assets it should immediately
> follow the underlying asset class standard in terms of time and other rules."*
>
> **Human directive 2026-09-27 (scope):** implement the correct structure, not the smallest
> one; use true/standard types regardless of current state.
>
> **Naming law:** `m2b.5_asset_class_standard.spec.md` — two levels per `check_naming.py`.

---

## 1. Purpose

Make adding or removing an instrument **a single edit**. Session hours, price precision and
quantity conventions must be *inherited from the asset class*, not retyped per asset.

## 2. The defect being fixed

`config/assets/*.yaml` hand-repeats six fields per instrument. The loader validates **none**
of them. So:

1. A new asset that omits `contract_size` loads successfully and yields `None` downstream —
   silent wrong margin math (`AGENTS.md` §3.1).
2. A new asset that omits `asset_class`, or misspells it, silently loses its session rule.
3. Nothing detects an override that **contradicts** its class (e.g. a crypto asset given
   forex session hours), so the calendar would happily report it as shut on Saturday.
4. **Money fields are authored as YAML floats.** `AGENTS.md` §3.1: no `float` in the money
   path, construct from `str`.

Session hours were *already* class-derived (`_CLASS_DEFAULTS`), but they lived in the
calendar while the master fields lived in YAML — two tables that could disagree.

## 3. The standard (single source of truth)

One module, `shared/reference/asset_classes.py`, imported by **both** the calendar and
the asset master. `modules → shared` is already permitted by `import-linter`; `shared.contracts`
must not import it, and does not need to.

| Class | Session (inherited) | Price dp | Qty step | Min qty |
|---|---|---|---|---|
| `crypto` | always open | 8 | 0.00001 | 0.00001 |
| `forex` | Sun 21:00 → Fri 21:00 | 5 | 0.01 | 0.01 |
| `commodity` | Sun 22:00 → Fri 21:00 | 2 | 0.01 | 0.01 |

### 3.0 THE STANDARD IS CONFIG, NOT CODE — human directive 2026-09-27

> *"everything that can be configured should be made configurable, especially things that
> have the potential to be expanded."*

A hardcoded `ASSET_CLASSES` dict in Python means **adding an asset class is a code change and
a deploy**. That is the wrong shape for something whose whole purpose is to grow. So:

- The table lives in **`config/reference/asset_classes.yaml`**.
- `shared/reference/asset_classes.py` owns the **schema and validation**, not the data, and
  performs **no import-time file I/O** — `shared` must not know the app's config path.
- `modules/reference/standards.py` owns the path and the single process-wide load, and is the
  one thing the calendar and the asset master both import.
- Adding an asset class, or changing any class's hours, is a **YAML edit**. No Python change,
  no test change, no deploy.

Config must still be **validated harder than code**, because a typo in data is invisible in
review while a typo in code is not:

| Failure | Behaviour |
|---|---|
| file missing | fail at boot, typed error — never fall back to a built-in default |
| class missing required key | fail at boot, naming file + class + key |
| `always_open` **and** weekday fields together | fail at boot — no single answer exists |
| `min_qty` > `qty_step` | fail at boot — no order could be placed |
| non-positive or non-finite money value | fail at boot |
| unknown key in a class entry | fail at boot — a typo must not be silently ignored |
| duplicate class name | fail at boot |

**Fail-closed on absence is the important one.** If the config cannot be read, there is no
safe default: guessing `forex` hours for an unknown class would trade an asset at the
weekend, or refuse to trade it while it is open.

### 3.1 Inherited vs overridable — the distinction that matters

Not everything can be class-level, and forcing it would be wrong:

| Field | Policy | Why |
|---|---|---|
| `asset_class` | **required**, from the canon | drives everything else |
| session hours | **inherited**, never per-asset | one venue, one session per class |
| `tick_size` | inherited default, **overridable** | crypto pairs differ in tick |
| `contract_size` | **per-instrument, always required** | Gold 100, Silver 5000 — genuinely not a class property |
| `min_qty`, `qty_step` | inherited, overridable | venue-specific lot rules |

A session override on an asset is **rejected**, not merged. Rationale: the whole point is
that session hours are a property of the class, so allowing a per-asset exception is how the
two tables drift apart again.

## 4. Money typing — the actual fix

- Money fields are authored as **quoted strings**: `tick_size: "0.01"`.
- The loader **rejects** an unquoted YAML number for these fields with a typed
  `AssetConfigError` naming the file and field. Rationale: by the time YAML has parsed
  `0.01` it is already a `float`, and `Decimal(str(0.01))` only recovers the author's
  literal because Python's `repr` happens to be shortest-round-trip. That is a coincidence,
  not a guarantee, and §3.1 asks for the real type.
- Values become `Decimal` built directly from the string. `get_instrument()` therefore
  returns `Decimal` for `tick_size` / `contract_size` / `min_qty` / `qty_step`.
- **Consumer impact: none.** `contract_size`, `min_qty` and `qty_step` have no reader today.
  `normalize.py` does `Decimal(str(entry["tick_size"]))`, which is a no-op on a `Decimal`.
  `capture.py` already accepts `Decimal | str | float`. This is a **minor contract bump**,
  not a breaking change.

## 5. Add / remove semantics

| Operation | Result |
|---|---|
| **Add** a crypto asset with only `asset_class` + `contract_size` + broker natives | session, tick, qty all inherited; **one edit** |
| **Add** with a float `tick_size` | rejected, typed error, file + field named |
| **Add** with unknown/missing `asset_class` | rejected |
| **Add** with a per-asset session override | rejected |
| **Remove** an asset | its session override and broker natives become **orphans**, reported at load |

Orphan detection exists because removal is the dangerous direction: a stale session override
for an instrument no longer in the master is a rule that still fires.

## 6. Invariants

1. Every instrument declares an `asset_class` from the canon. No default, no fallback.
2. Every instrument has a `contract_size`, always `Decimal`, always `> 0`.
3. Money fields are never `float` or `int` at the boundary.
4. Session hours come from the class; a per-asset session override is an error.
5. The calendar and the asset master read the **same** standard object — one table, and a
   test that fails if they ever diverge.
6. Removing an asset cannot leave a live rule behind unreported.
7. A structurally invalid asset file stops the process at load, never at query time.

## 7. Edge cases

| # | Case | Required behaviour |
|---|---|---|
| E1 | crypto asset, only class + contract_size | 24/7 session inherited |
| E2 | forex asset, minimal entry | Sun 21:00→Fri 21:00 inherited |
| E3 | commodity asset, minimal entry | Sun 22:00→Fri 21:00 inherited |
| E4 | `tick_size: 0.01` unquoted | rejected, typed error |
| E5 | `tick_size: "0.01"` quoted | `Decimal("0.01")` |
| E6 | `contract_size` absent | rejected |
| E7 | `contract_size: "0"` or negative | rejected |
| E8 | `asset_class: "forexx"` | rejected, unknown class |
| E9 | `asset_class` absent | rejected |
| E10 | per-asset `sessions:` override present | rejected |
| E11 | `tick_size: "0.00001"` on crypto | accepted, override honoured |
| E12 | `min_qty` > `qty_step` | rejected (impossible lot ladder) |
| E13 | orphan session override for a removed asset | reported at load |
| E14 | orphan `broker_native` entry | reported at load |
| E15 | `Decimal("0.1") + Decimal("0.2")` | exact `0.3` — no float drift |
| E16 | tick_size with more precision than a float holds | exact, no rounding |
| E17 | unknown class in the standard itself | impossible; validated at import |
| E18 | standard and calendar diverge | a test fails (invariant 5) |

## 8. Adversarial

| Input | Must |
|---|---|
| `"1e400"` as tick_size | `Decimal` overflow handled → rejected, not `Infinity` |
| `""` empty string | rejected, typed error |
| `" 0.01 "` whitespace | rejected (no silent strip of author intent) |
| `"0.01USD"` | rejected |
| negative zero `"-0"` | rejected |
| duplicate instrument across two files | rejected, both files named |
| a class whose session is `always_open` AND has weekday fields | rejected as contradictory |
| 10,000-line asset file | loads without recursion error |

## 9. Definition of correct

- Adding a crypto asset requires writing `asset_class`, `contract_size` and broker natives.
  Nothing else. Its session is 24/7 and its tick/qty come from the class.
- An unquoted money field cannot reach the system.
- `Decimal("0.1") + Decimal("0.2") == Decimal("0.3")` for every money field.
- Gold and Silver keep **different** contract sizes (100 / 5000) without either being
  "wrong" — the standard does not flatten a real difference.

## 10. Tests required before implementation

`tests/m2b/test_m2b.5_spec.py` — one per §7 edge, the §8 adversarial set, and three
hand-calc asserts on money typing:

```python
# Hand calc: tick_size "0.00000001" stays 8dp; float(1e-8) would not round-trip identically
assert get_instrument(...)["tick_size"] == Decimal("0.00000001")
# Hand calc: 0.1 + 0.2 == 0.3 exactly in Decimal, 0.30000000000000004 in binary float
assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
```

Plus the **add/remove propagation** test, which is the point of the whole slice.

## 11. Line budget

Target ≤260 implementation lines across `shared/reference/asset_classes.py` plus the
edits to `reference.py` and `calendar.py`. Over-budget is a `§3.14` deviation, flagged
before acceptance.

## 12. Open questions

1. **Should `contract_size` become mandatory for every class?** Recommendation **yes** — a
   missing contract size is a guaranteed wrong notional, and there is no safe default. A
   `Decimal("1")` fallback would silently under-size Gold by 100x.
2. **Precision (`price_dp`) is not yet consumed** by any rounding path — `normalize.py` uses
   `tick_size`. Recommendation: keep `price_dp` in the standard as documentation-of-record
   and derive the *default* `tick_size` from it, but do not add a second rounding path yet.
3. **The 20 unquoted money fields in the 3 existing YAML files were quoted** as part of this
   slice, so the shipped config obeys the rule the slice enforces.
4. **Calendar ↔ master coupling (found by the add/remove proof, 2026-09-27).**
   `TradingCalendar._class_of()` resolved the asset class through the module-global
   `get_instrument`, so a newly added instrument was invisible to the calendar — the
   add/remove proof raised `UnknownInstrumentError` for an asset that had just been added
   successfully. Fixed by making the class resolver **injectable**, defaulting to the
   reference module. A hidden global is untestable without monkeypatching, and this system
   forbids flaky/incidental test setups.
5. **Other candidates for configuration, to sweep in a later slice** (recorded, not built):
   backfill horizon and request cap, kill-switch circuit-breaker thresholds, calendar
   holidays, coverage/gap state machine bounds. Each is a number that will need to change
   without a deploy.
