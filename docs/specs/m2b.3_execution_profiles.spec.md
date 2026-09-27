# Spec: M2b.3 Execution Profiles — descriptive cost facts (facts only, no routing)

Purpose: Load + serve **effective-dated execution cost facts** per `(instrument_id, broker)` —
`commission_rate`, `typical_spread`, `financing_apy`, `slippage` — as frozen `Decimal` models,
so M8's cost model (AGENTS §3.4: *fees + spread + slippage_params* from M2b, never free
backtests) and M6's future `smart_router` (facts only) read one source.
Bible ref: `02b_M2b_Reference.md` §`get_execution_profile("FX.EURUSD", broker="deriv",
effective_at=ts) -> {commission, financing, slippage_params}` (lines 132/245) + **M2b-I05**
(`commission=X` fact, never `route to deriv` — `grep M2b -r "route"` must be 0) + P06.
Gate: G0 needs honest costs before `expectancy-after-fees` means anything.

## Inputs

- `config/reference/execution_profiles.yaml` (NEW, repo-local; sibling of `snapshot.yaml`):

  ```yaml
  profiles:
    - instrument_id: CRYPTO.BTCUSD        # must exist in config/assets master (identity = SoT)
      broker: deriv                        # one of deriv | mt5 | headway
      effective_from: 2026-09-26T00:00:00+00:00
      commission_rate: "0.0000"           # fraction of notional, QUOTED decimal string
      typical_spread: "10.0"              # quote currency PER BASE (price units)
      financing_apy: "-0.0200"            # annualized descriptive rate, may be negative
      slippage:
        model: fixed_bps                   # fixed_bps | none  (Day-1 pins these two)
        value: "0.50"                      # bps of notional, QUOTED decimal string
  ```

- `load_execution_profiles(path) -> dict[tuple[str, str], list[ExecutionProfile]]` — explicit-path
  loader (tests/tools pass tmp files); raises `ReferenceValidationError` on ANY invalid content.
- `get_execution_profile(instrument_id, broker, effective_at=None) -> ExecutionProfile` — facade
  on the repo file (re-exported from `modules/reference/reference.py`, LRU-cached like
  `current_snapshot_id()`; rotation = edit file + `get_execution_profile.cache_clear()`).
  `effective_at=None` → wall-clock now (UTC). Sync, no I/O in the lookup beyond first load.

## Outputs

- `SlippageParams` (frozen Pydantic, `extra="forbid"`): `model: Literal["fixed_bps","none"]`,
  `value: Decimal` (bps; must be `0` when `model="none"`).
- `ExecutionProfile` (frozen Pydantic, `extra="forbid"`): `instrument_id: str`, `broker: str`,
  `effective_from: datetime` (tz-aware UTC), `commission_rate: Decimal` (≥0),
  `typical_spread: Decimal` (≥0, quote-ccy per base), `financing_apy: Decimal` (signed),
  `slippage: SlippageParams`.
- Units/sign once: rates are fractions (0.0001 = 1 bp); spread is price units; PnL sign
  conventions untouched (this slice computes NOTHING — I05 facts only).

## Edges (each = one test, named after the edge)

1. File missing / top-level not a mapping / `profiles` key missing → `ReferenceValidationError`
   (fail-closed: no costs file, no backtest — never an implicit zero-cost profile).
2. `profiles: []` (empty list) → `ReferenceValidationError` (costs must be explicit).
3. Unknown `instrument_id` (not in `config/assets` master) → `UnknownInstrumentError`.
4. Instrument known, broker not in master's `brokers` for it → `ReferenceValidationError`
   ("no execution profile for (inst, broker)"), even if other brokers have rows.
5. Broker not in {deriv, mt5, headway} → `UnknownBrokerError`.
6. `effective_at` naive (no tz) → `ReferenceValidationError` (UTC law, AGENTS §3.8).
7. `effective_at` earlier than every row's `effective_from` → `ReferenceValidationError`
   (never silently use a future profile; fail closed).
8. `effective_at` later than all rows (incl. year 3000) → the latest row (normal path).
9. Overlap resolution: several rows for one key → **latest `effective_from` ≤ `effective_at`**
   wins (effective-dating precedence).
10. Two rows for one key with the SAME `effective_from` → `ReferenceValidationError`
    (ambiguous; never last-wins).
11. A row's `instrument_id` exists nowhere in the master → `ReferenceValidationError`
    (config drift vs identity SoT — caught at load).
12. Bounds at load (§3.13 refuse, never clamp): `commission_rate` 0..0.10;
    `typical_spread` 0..1_000_000; `financing_apy` -1..1; slippage `value` 0..1000;
    `effective_from` must be tz-aware UTC; out-of-bounds → `ReferenceValidationError`.
13. Negative `commission_rate` or `typical_spread` → `ReferenceValidationError`.
14. Wrong types: commission `"abc"` / `null` / list → `ReferenceValidationError`
    (Decimal parse / isinstance checks — YAML floats coerced via `Decimal(str(v))`).
15. `ExecutionProfile` is frozen: attribute assignment raises (pydantic `ValidationError`).
16. Facade caching: after `get_execution_profile.cache_clear()`, an edited file is re-read
    (rotation precedent = `current_snapshot_id`, M2b-I08 determinism).
17. `model="none"` requires `value` exactly `Decimal("0")`; `model="fixed_bps"` requires
    `value > 0` → else `ReferenceValidationError`.

## Adversarial (not in the edges; must fail safely)

- A1 `instrument_id = "../../etc/passwd"` → `UnknownInstrumentError` — pure dict key, zero
  filesystem involvement (loader reads ONE fixed path; lookup never touches `Path`).
- A2 Extra YAML key `route: mt5` on a row → `ReferenceValidationError` (`extra="forbid"`)
  — structural proof that routing decisions cannot creep into M2b (I05).
- A3 `commission_rate: 1.0e99` → bounds `ReferenceValidationError`, no clamp; `1.0e-99`
  is INSIDE bounds 0..0.10 → accepted and stored exactly `Decimal("1e-99")`
  (float shortest-repr roundtrip proof, no precision loss).
  *(REV2, post-A72 builder amendment, recorded transparently: original A3 wrongly claimed
  `1e-99` fails bounds — it does not. `1e+99` reject unchanged; `1e-99` flipped to exact-accept.)*
- A4 Source-level no-routing: `execution_profiles.py` source contains no substring `"route"`
  (mirrors bible's grep law as a test, like existing guard-style tests).

## Correct / money law

- **REV3 (2026-09-27, human-approved) — MONEY VALUES MUST BE QUOTED STRINGS.** This reverses
  REV2's "YAML floats coerced via `Decimal(str(v))`". Rationale: by the time YAML has parsed
  an unquoted `0.0000001` it is already a binary `float`, and `Decimal(str(f))` recovers the
  author's literal only because CPython's float `repr` is shortest-round-trip — a property of
  the runtime, not a guarantee of the format. A money law that depends on an undocumented
  runtime property is not a law. An unquoted money value is now **rejected** with a message
  naming the field and telling the author to quote it. `M2b.5` already enforces exactly this
  in `shared/reference/asset_classes.py`; without REV3 the system carried **two opposite
  money-parsing laws**, and whichever slice landed last would have silently won.
- **Consequence for A3 and H2 (recorded transparently, not silently):** an unquoted
  `1.0e-99` and an unquoted `0.0000001` are now **rejected on representation grounds**. This
  is a cleaner rule than REV2's, which turned on magnitude: the question is no longer "is this
  number small enough to be safe" but "is this the author's literal or a float we guessed at".
  To store either value, quote it: `"0.0000001"`, `"1e-99"`.
- ALL money-adjacent numbers are `Decimal` parsed **from strings** — **no float ever enters a
  stored field**. No rounding applied (facts stored exact as given, including trailing zeros
  and exponent).
- Hand-calc asserts (3, exact `Decimal` equality, no `approx`):
  - `commission_rate: "0.0001"` → `Decimal("0.0001")`; times notional `Decimal("84000")`
    → illustrative cost `Decimal("8.40")` (printed in verify script only — computation
    belongs to M8, NOT this slice).
  - **H2 (REV3): unquoted `0.0000001` is REJECTED; quoted `"0.0000001"` →
    `Decimal("0.0000001")` exactly.** (REV2 asserted the float roundtrip succeeded — the
    rejection is the assertion now.)
  - `typical_spread: "0.00010"` → `Decimal("0.00010")` (trailing zero preserved, exact parse).

## Contract

No `shared/contracts` change, **no bump**: frozen in-module models (precedent: M2b.2
`load_snapshot_id`, M4.3 in-module `SignalRejection`); typed errors REUSED from
`shared.contracts.errors` (`ReferenceValidationError`, `UnknownInstrumentError`,
`UnknownBrokerError`) — adding a new error class would be a frozen-contract change → banned
in this slice. Consumers (future, listed for §5): M8 backtest cost model, M6 smart_router,
`scripts/verify_m2b.3_*.py`.

## Out of scope

- Applying costs to trades/fills (M8.1's job), fill models, expectancy math.
- `broker_capabilities` / `contract_specs` / `reference_validator` (P06 siblings — own slices).
- Routing of any kind (I05: `grep M2b -r "route"` = 0).
- Per-tenant anything (facts are global; storage law §4.3).
- Editing `config/assets/*.yaml` (identity SoT read-only here).

## Test plan (Step 3, BEFORE code) — `tests/m2_market_data/test_m2b.3_spec.py`

- One test per edge 1–17 (named after the edge) + adversarial A1–A4.
- 3 hand-calc Decimal asserts per Money law (exact equality, `# Hand calc:` comments).
- Happy path: load tmp file → get with `effective_at` mid-history → exact frozen field values.
- Precedence pair: two `effective_from` versions → assert the boundary (before v2 = v1,
  exactly at v2 = v2).
- Honest RED expected: `ModuleNotFoundError: modules.reference.execution_profiles`
  (facade import) captured before Step 5; ruff 0 on the file.
- Naming: spec `m2b.3_execution_profiles.spec.md`, test `test_m2b.3_spec.py`,
  verify `verify_m2b.3_execution_profiles.py` — all pass `check_naming.py`.

## Verify plan (Step 7, real data) — `scripts/verify_m2b.3_execution_profiles.py` ≤250 lines

- V1 load the SHIPPED `config/reference/execution_profiles.yaml`; print every profile as a
  table (instrument, broker, effective_from, all Decimal fields verbatim).
- V2 3 typed-error probes against tmp files (missing file / unknown instrument / negative
  commission) → assert exact exception types, print them.
- V3 illustrative cost hand-check printed with exact Decimal math (1 BTC notional × commission
  + half-spread + slippage bps) — EYEBALL number vs the printed hand arithmetic.
- V4 effective-dating probe: `effective_at` spanning two versions → prints which row won.
- EXIT 0 = all probes typed-correct; human confirms seed values against broker docs
  (seed numbers are DAY-1 PLACEHOLDERS, flagged in the YAML header comment).

## Human gates

- This spec LGTM (Step 1) → tests LGTM (Step 4) → impl → verify eyeball (Steps 7/8 — money
  facts → hand-check required, §3.1). Seed-value sign-off folded into verify eyeball.
