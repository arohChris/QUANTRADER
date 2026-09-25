# Financial Analyst App — QUANTRADER2 (Gate G0)

> **Bible:** `System_Development_Manual/System_Development_Manual.md` (4,300 lines) is the law; `AGENTS.md` governs *how* we build and wins on conflict. If code contradicts bible, code is wrong.
> **Status authority:** `BUILD_STATUS.md` (repo root) — nothing is `complete` until that file says so.

## What is M1?

M1 is the **Shared Kernel Foundation Library** — the law every other module imports. It does NOT trade, does NOT fetch data, does NOT calculate RSI. It tells *how* every other module should behave.

- **M1.1 Contracts** (`shared/contracts/`) — **10** frozen versioned Pydantic `BaseContract` schemas: `MarketData`, `AnalyticsResult`, `Signal`, `TradePlan`, `PortfolioState`, `RiskDecision`, `OrderIntent`, `Order`, `ExecutionResult`, `ReconciliationReport`. All frozen, versioned, `correlation_id` traced. *(Earlier README said "8" and omitted `OrderIntent`.)*
- **M1.2 Core Runtime** (`shared/core/`) — `BaseEngine(async start/stop/health)`, `Clock(System/Simulated)`, `Lifecycle(startup/shutdown order)`, `Health`, `Logging(JSON+correlation_id)`
- **M1.3 Config & Environment** (`config/`) — `Settings` from YAML + `environments/*.yaml` overlay + `feature_flags` — fail loud on missing key
- **M1.4 Security & Isolation** (`shared/core/tenant_context.py`, `permissions.py`, `broker_vault.py`, `retry_policy.py`) — `user_id+broker_id` via `ContextVar`, RBAC, Fernet vault, jitter retry + dead-letter

## Tenant Isolation (Institutional Requirement)

Every DB query is scoped: `WHERE user_id=:uid AND broker_id=:bid` via `tenant_context`.
Deriv user sees/stores/processes ONLY Deriv. No cross-broker or cross-user leakage.

```python
from shared.core.tenant_context import tenant_context

# Middleware — set once per request
tenant_context.set(user_id="42", broker_id="deriv")
try:
    positions = db.query("SELECT * FROM positions WHERE user_id=:uid AND broker_id=:bid",
                         uid=tenant_context.require_user_id(), bid=tenant_context.require_broker_id())
finally:
    tenant_context.clear()
```

## Speed Budgets (P95)

| Hop | Budget |
|-----|--------|
| tick → parquet | <50ms |
| analytics | <20ms |
| risk | <10ms |
| order → ack | <500ms |

Enforced via `shared/core/clock.py` + `anyio` async + `Redis` cache.

## Install & Test

```bash
cd financial_analyst_app
pip install -e ".[dev]"
pytest -q                          # full suite (expect collection RED until M3.0 impl — see BUILD_STATUS)
pytest tests/ci -q                 # CI-guard self-tests (must always be green)
python scripts/check_structure.py  # bible §3 enforcement
python scripts/check_build_order.py && python scripts/check_naming.py
python scripts/check_pit_guard.py && python scripts/check_retention.py
python -m importlinter.cli         # (lint-imports wrapper; entry point not on PATH here)
```

## Build Independence (Airplane Test)

```bash
pytest tests/contract_tests -q     # contract invariants (NO snapshot mechanism exists yet —
                                   # README previously documented `--snapshot`, which pytest rejects:
                                   # "unrecognized arguments"; see BUILD_STATUS Discrepancies / S6)
pytest tests/test_tenant_isolation.py -v       # isolation
pytest tests/test_clock.py -v                  # SimulatedClock deterministic
pytest tests/test_config.py -v                 # bad YAML → loud fail
# No live broker WS, no real DB — M2 is proven against fixtures + tmp dirs only
# (see BUILD_STATUS Known Bugs: data layer has never held real data)
```

## Build Order (per `BUILD_STATUS.md` — that file wins)

- **P05-P07** M2b Reference (instrument_master, symbol_mapping, contract_specs) — `complete`
- **P08-P14** M2 Data (ingestion → storage) — `complete`, **M2.7 FROZEN**
- **P15** M3.0 Capability — **current**: spec + 24 tests written, **ACTION 29 (Step 4 test LGTM) open**
- **P16+** M3.1 Feature Engine / M3.2 indicators → **P22** M4.1 Strategy Registry → M4.5 TradePlan — **blocked** until M3.0 is `complete` (AGENTS §1 Rule 5)

See `System_Development_Manual/modules/` for per-module expanded discussions (2,841 lines).

## Vault

```bash
# Set key (32 url-safe base64) — never commit
export VAULT_KEY="your-32-byte-base64-key-here"
```

Tokens are encrypted per `user_id+broker_id` and never appear in logs.

## The Final Rule

This codebase is **CI-enforceable** — 5 gates:
1. `check_structure.py` — folder tree matches §3
2. `import-linter` — §33 dependency direction
3. `contract_tests` — §5 immutability
4. `pytest --cov` — >85% for M1
5. Every `modules/<name>/README.md` has 9 required headings

If any fails, merge is blocked.
