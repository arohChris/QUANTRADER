# Contract Change Proposal — M1.1 v1.0 → v2.0 (AGENTS.md §5)

Status: **AWAITING HUMAN SIGN-OFF.** No frozen contract has been modified.
Raised: 2026-09-27 · Gate G0 · Slice: `m1.1` contract remediation

---

## Change

Six changes to 7 frozen contracts, in one major bump. Money types first, because they are the
correctness blocker; the rest are the tenant/auditability defects they sit alongside.

### 1. Money fields `float` → `Decimal`

| Contract | Fields |
|---|---|
| `Position` | `qty`, `avg_entry`, `unrealized_pnl`, `realized_pnl` |
| `PortfolioState` | `cash`, `equity`, `margin_used`, `margin_available`, `exposure`, `leverage` |
| `OrderIntent` | `qty`, `limit_price`, `stop_loss`, `take_profit` |
| `Order` | `qty`, `price`, `stop_loss`, `take_profit` |
| `RiskDecision` | `approved_qty` |
| `Fill` | `price`, `qty`, `commission` |
| `ExecutionResult` | `avg_fill_price`, `slippage_bps` |

`leverage` divides *into* margin and `slippage_bps` *is* a cost, so both enter money arithmetic and
cannot be the one float that poisons a `Decimal` expression.

**Deliberately unchanged (`float`) — documented, not accidental:** `Provenance.signal_strength`
(a bounded score), `ExecutionResult.latency_ms`, `RiskDecision.latency_budget_ms` (durations).
None enters money arithmetic.

### 2. Required tenant identity (was `str | None = None`)

`PortfolioState`, `Order`, `OrderIntent` gain a **required** `trading_account_id: UUID`. Optional
tenant fields on a private contract are a live cross-tenant-leak path: a `PortfolioState(user_id=None)`
is currently constructible, so an `if user_id is None: load_all()` branch is reachable.

`user_id`/`broker` are **retained** (not removed) for backward-compatible reads, but no longer
default to `None`. `broker_id` → `broker_account_id` is deliberately **NOT** in this bump; it is D24
and needs its own slice with the RLS schema, because a user with two accounts at one broker is the
reason it exists.

### 3. `Position` and `Fill` inherit `BaseContract`

They are currently plain `BaseModel`, so they carry no `version`, `correlation_id`, `trace_id` or
`timestamp`. A `Position` therefore **cannot be traced to a trade**, which breaks the §3.12 audit
chain (`grep correlation_id=123` must find tick → … → ledger). Inconsistent with the other 7 contracts.

### 4. `Fill.timestamp: str | None` → aware `datetime`

It is a **string**. §3.8 requires UTC `datetime(tzinfo=timezone.utc)`, never naive, never a string.
A string field invites `timestamp[:10]` slicing, which breaks at timezone edges. Deriv sends epoch
integers, so the epoch→UTC conversion becomes explicit and tested.

### 5. `RiskDecision` gains tenant + the money it evaluated

It currently has **no `user_id`, no `broker`, no `balance`, no `required_margin`**. §3.12 requires
every risk decision logged "with exact numbers: balance, required_margin, approved_qty" and
attributable to a user. Right now **a risk decision cannot be reconstructed** — and this is the
contract under mandatory human review (§2.8). Adds: `trading_account_id`, `balance`,
`required_margin` (both `Decimal`), all required.

### 6. `Order`/`OrderIntent` `symbol` → `instrument_id`

M2 and M2b use `instrument_id` + separate `broker` everywhere. Passing a broker-native symbol
(`frxXAUUSD`) where a canonical id belongs is precisely the bug M2b exists to prevent. Rename only;
`broker` stays a separate field.

---

## Why (evidence, not assertion)

1. **§3.1 compliance.** 20+ money fields are `float`. §10 lists "use `float` for money" as an
   automatic rejection.
2. **The law currently contradicts itself.** §2 Step 3 mandates `assert result == Decimal("12.00")`
   for money and §2 Step 4 *rejects* `pytest.approx` for money. With a `float` field, Pydantic
   coerces the `Decimal` away, so **the mandated hand-calc assert is unwritable** and the only
   remaining option is the method the law forbids. Fixing the contract makes the law executable.
3. **The error is undetectable after the fact.** Measured: `str(2000000075959.342)` prints
   `'2000000075959.342'`; only direct conversion reveals `2000000075959.342041015625`. Python's
   float repr uses shortest-round-trip, so it **hides** the error. Money precision must be enforced
   by type at the boundary, which is exactly what this change does.
4. **`RiskDecision` is unauditable** (item 5) — a direct §3.12 violation on the one contract under
   mandatory review.
5. **The money authority now exists and nothing uses it.** `shared/core/money.py` landed in `33a7301`
   with 52 tests and 34/34 real-data checks. Until these contracts adopt `Decimal`, that authority
   protects nothing downstream.

## Impact — every dependent module, per §5

**Verified by grep across `modules/`, `shared/`, `app/`, `tests/`, `scripts/`:**

| Contract | Import sites | Production consumers |
|---|---|---|
| `contracts.portfolio` | 1 | **0** |
| `contracts.order` | 1 | **0** |
| `contracts.risk` | 1 | **0** |
| `contracts.execution` | 1 | **0** |

The **only** consumer of all four is `tests/contract_tests/test_contracts.py` (25 test functions).
No module in `modules/` or `app/` constructs `PortfolioState`, `Order`, `OrderIntent`,
`RiskDecision`, `ExecutionResult`, `Fill`, or `Position`.

**This is the cheapest possible moment to do it.** M5/M6/M7 have not started building these
contracts. Once they do, the identical change becomes a breaking change with real consumers and a
migration burden. It also explains D34 and D35: the contracts were declared, frozen, and then
**never exercised**, so float money and the missing tenant field were invisible for months.

Consumers to notify: **none in production.** `M4`, `M5`, `M6`, `M7` are the intended future
consumers and are not yet implemented, so there is nothing to break today.

## Bump

**Major `1.0 → 2.0`.** Justification: `float → Decimal` is not backward-compatible under equality
(`Decimal("0.4") != 0.4` in type, and Pydantic's coercion behaviour changes), and the JSON wire
format changes — `Decimal` serialises as a **string** (`{"qty":"0.4"}`), not a number. A minor bump
would be a lie.

## Migration

- `shared/contracts/migrations/v1_0_to_v2_0.py` — upgrades persisted v1.0 rows. **No stored data
  exists yet** (no `positions`/`orders`/`ledger` tables), so the migration is structural only and is
  testable against synthetic v1.0 fixtures.
- A **contract snapshot** must be created. **Note: none exists today** — `tests/contract_tests/`
  contains only `test_contracts.py`, so the §5/§8 gate
  `pytest tests/contract_tests --snapshot` cannot currently fail on an un-bumped add. That gate is
  aspirational until this bump adds the snapshot. Filed as **D43**.
- `test_contracts.py` (25 functions) must be updated; several currently assert float behaviour and
  one (`test_contracts.py:164`) constructs `PortfolioState(user_id=..., broker=..., equity=1000, …)`
  which becomes `Decimal` and a required `trading_account_id`.

## Test plan (written before implementation, per §2 Step 3)

`tests/contract_tests/test_m1.1_v2_spec.py`, against this proposal:

- one test per changed field, asserting the **type** is `Decimal` and the value is exact
- **`float` input to every money field is rejected**, not coerced (adversarial)
- a `PortfolioState` with no tenant cannot be constructed (adversarial)
- `Position`/`Fill` carry `correlation_id` and `version`, and the value **round-trips**
  through `json.dumps`/`loads` as a string with no precision loss
- `Fill.timestamp` is timezone-aware UTC; a naive datetime is refused (adversarial)
- `RiskDecision` without `balance`/`required_margin`/`trading_account_id` cannot be constructed
- `model_dump_json()` emits money as a **string**; `json.loads` → `Decimal` is exact
- hand-calcs: `assert approved_qty == Decimal("0.4")` and a PnL assert to the cent
- a cross-check against a `Fraction` oracle (§3.5 second implementation)
- the v1.0 → v2.0 migration upgrades a synthetic v1.0 row with **zero** precision loss
- snapshot test: an un-bumped field add **fails**

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Parquet/JSON wire format changes to string | Medium | No stored data exists; snapshot test pins the format |
| Pydantic coerces `float` → `Decimal` in lax mode, so a float *may* be accepted silently | **Medium** | `test_m1.1_v2_spec.py` asserts rejection; if Pydantic's lax coercion proves to defeat this, a field validator is required (decided at implementation, not guessed now) |
| `required_margin`/`balance` required on `RiskDecision` breaks future M5 construction | Low | M5 unbuilt; it is being built to this shape |
| Scope creep into D24 (`broker_account_id`) | Medium | Explicitly **excluded** from this bump |

## Sign-off

```
Change:   float -> Decimal (7 contracts) + required trading_account_id
          + BaseContract on Position/Fill + datetime timestamp
          + RiskDecision tenant/balance/required_margin + symbol -> instrument_id
Why:      §3.1 compliance; the mandated Decimal hand-calc assert is currently
          UNWRITABLE; §3.12 auditability of RiskDecision; money.py exists and
          protects nothing until contracts adopt it
Impact:   tests/contract_tests/test_contracts.py (25 fn) ONLY.
          ZERO production consumers - verified by grep, not assumed.
Bump:     major 1.0 -> 2.0 (equality semantics + JSON wire format both change)
Migration: shared/contracts/migrations/v1_0_to_v2_0.py + new contract snapshot (D43)
Consumers notified: none in production; M4/M5/M6/M7 are unbuilt
Sign-off: [ ] PENDING HUMAN
```
