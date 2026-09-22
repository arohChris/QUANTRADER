# Spec: M2b.1 Instrument & Symbol Identity

Purpose: Map broker-native symbols to canonical instrument IDs and resolve instrument master facts via a sync ReferenceService facade.
Bible ref: System_Development_Manual/modules/02b_M2b_Reference.md §1, §4, §5, P05 gate.
Scope note (2026-09-22, human): supported asset classes are **forex | crypto | commodity only** — index/equity/synthetic removed from product scope.

## Inputs
- `native: str` — broker-native symbol (e.g. `frxEURUSD`, `EURUSD`, `frxXAUUSD`, `XAUUSD`, `cryBTCUSD`)
- `broker: str` — one of `deriv | mt5 | headway` (lowercase)
- `instrument_id: str` — canonical namespaced id (e.g. `FX.EURUSD`, `COMMODITY.XAUUSD`, `BTCUSD`)
- Asset YAML fixtures: `config/assets/forex.yaml`, `config/assets/crypto.yaml`, `config/assets/commodities.yaml` (`synthetic.yaml` removed)

## Outputs
- `to_canonical(native, broker) -> str` — canonical `instrument_id`
- `to_native(instrument_id, broker) -> str` — reverse mapping
- `exists(instrument_id, broker=None) -> bool`
- `get_instrument(instrument_id) -> dict` — `{asset_class, canonical_symbol, display_name, brokers, ...}`
- All lookups **sync, deterministic, pure dict/cache** — no `await`, no I/O in hot path.

## Invariants (M2b-I01, I02, I06, I08)
- I01: every supported instrument has exactly one `canonical_instrument_id`.
- I02: `to_canonical(to_native(id, broker), broker) == id` for every **explicitly mapped** `(instrument_id, broker)` pair — i.e. every pair present in the YAML mapping tables. Unmapped brokers/symbols are outside I02's scope (see Edges: passthrough).
- I06: cache key is `(instrument_id, broker)` — no `user_id`.
- I08: same inputs → same output, always (golden tests).

## Asset classes (locked vocabulary — `AssetClass` enum)
- Known values: `forex | crypto | commodity` **only** (index/equity/synthetic removed 2026-09-22 per human).
- Every instrument YAML entry's `asset_class` MUST be one of the above — unknown → validation fail (P06 publishes; here asserted detectable via `get_instrument`).
- Canonical ID namespaces for instruments in this sub-module: `FX.`, `CRYPTO.`, `COMMODITY.` (prefix = asset class, uppercase, dotted). Crypto uses `CRYPTO.BTCUSD` as `instrument_id` with `canonical_symbol: BTCUSD` (display) — REV1 2026-09-22 (human M2.2 gate: bare `BTCUSD` is not a valid instrument_id).

## Edges
- `native` unknown for broker → **return native unchanged** — this is an **unresolved native symbol** (passthrough per manual pseudocode `maps[broker].get(native, native)`), NOT a resolved canonical ID and NOT an exception. Callers/downstream **M2.2** are responsible for detecting unresolved results (result does not match a known canonical prefix/ID) and rejecting/quarantining them; M2.3 is the authoritative data-quality gate for symbols that make it that far.
- `broker` unknown (e.g. `coinbase`) → raise typed error `UnknownBrokerError` (not silent `{}`).
- `instrument_id` unknown in `get_instrument` → raise `UnknownInstrumentError`.
- `to_native(instrument_id, broker)`:
  - unknown `instrument_id` → `UnknownInstrumentError`;
  - known `instrument_id` but no `broker_native` pair for that broker (e.g. `to_native("FX.GBPUSD", "headway")`) → `ReferenceValidationError` (safe typed failure; pinned F4 review 2026-09-22).
- `exists(instrument_id, broker="coinbase" | "")` → **False** (does not raise; asymmetry with `to_canonical` unknown-broker raise is intentional — F5 review 2026-09-22).
- `exists("FX.EURUSD", broker="mt5")` → True; `exists("FX.EURUSD", broker="deriv")` → True; `exists("FX.GBPUSD", broker="headway")` → False (not in brokers list).
- `exists("COMMODITY.XAUUSD", broker="deriv")` → True; `exists("COMMODITY.XAUUSD", broker="mt5")` → True; `exists("COMMODITY.XAUUSD", broker="headway")` → False (not in brokers list).
- `exists(instrument_id, broker=None)` → True if instrument in master regardless of broker.
- Empty string `native=""` or `broker=""` → raise `ValidationError`-style typed error (adversarial).
- Case sensitivity: symbols are case-sensitive as stored (`frxEURUSD` ≠ `frxeurusd`, `frxXAUUSD` ≠ `frxxauusd`); no silent lowercasing.
- Round-trip: every explicit mapping must satisfy I02 both directions.

## Correct
- Mapping tables loaded from config YAML keys (`broker_native` field) — single source; no hard-coded `if symbol=="..."` elsewhere.
- `broker_native` shape: either a single string (one native alias for all brokers listed) **or** a mapping `{broker: native}` when natives differ per broker (e.g. forex, commodity).
- P05 gate: `to_canonical("frxEURUSD", "deriv") == "FX.EURUSD"`.
- Also: `to_canonical("EURUSD", "mt5") == "FX.EURUSD"`; `to_canonical("frxGBPUSD", "deriv") == "FX.GBPUSD"`.
- Commodity: `to_canonical("frxXAUUSD", "deriv") == "COMMODITY.XAUUSD"`; `to_canonical("XAUUSD", "mt5") == "COMMODITY.XAUUSD"`; `to_canonical("XAGUSD", "mt5") == "COMMODITY.XAGUSD"`.
- Crypto: `to_canonical("cryBTCUSD", "deriv") == "CRYPTO.BTCUSD"`; `to_canonical("BTCUSD", "mt5") == "CRYPTO.BTCUSD"` (mt5 native string equals display symbol but resolves to namespaced id).
- `get_instrument("COMMODITY.XAUUSD")["asset_class"] == "commodity"`; `canonical_symbol == "XAUUSD"`; `display_name == "Gold"`.
- No money math in this sub-module (hand-calc N/A). Commodity `tick_size` / `contract_size` values in YAML are **fixtures for identity only** — authoritative specs land in P06 (`contract_specs`), not here.

## Adversarial (not in edges above)
- `to_canonical("'; DROP TABLE instruments;--", "deriv")` → passthrough string, never executed / never matches.
- `to_canonical(None, "deriv")` → typed error, not `None` return.
- Mapping target that is not in instrument_master → startup/validation fail (validator is P06; here: `exists` on mapped id returns False and is asserted in tests as detectable).

## Contract note
- **`AssetClass` enum is a frozen shared contract** (`shared/contracts/instrument.py`, package `2.0.0`): vocabulary `forex|crypto|commodity` **only** — major cut 2026-09-22 (index/equity/synthetic removed). Consumers: M2b (loader/validator), M2.2, M3 (`get_profile(asset_class)`), M5, M6, M7, M8.
- **Typed reference errors** live in `shared/contracts/errors.py` (additive, package history `1.2.0`): `ReferenceError` base → `UnknownBrokerError`, `UnknownInstrumentError`, `ReferenceValidationError`. Consumers: M2b facade, M2.2, tests.
- Full frozen `Instrument` Pydantic model deferred — identity API returns `dict` per manual §4; enum alone locks vocabulary without over-freezing the facade.
- `ReferenceService` remains a plain Python facade (module), not a shared contract.
- Package bump history: 1.0.0 → 1.1.0 (AssetClass) → 1.2.0 (typed reference errors) → 1.3.0 (Drop) → **2.0.0 (AssetClass major scope cut)**.
