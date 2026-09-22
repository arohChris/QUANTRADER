# Financial Analyst App — M1 Foundation (v0.1-foundation)

> **Bible:** `System_Development_Manual/System_Development_Manual.md` v1.3 BEST APPROACH (12 modules, 57 phases, 3,691 lines) is the law. If code contradicts bible, code is wrong.

## What is M1?

M1 is the **Shared Kernel Foundation Library** — the law every other module imports. It does NOT trade, does NOT fetch data, does NOT calculate RSI. It tells *how* every other module should behave.

- **M1.1 Contracts** (`shared/contracts/`) — 8 frozen versioned Pydantic schemas: `MarketData`, `AnalyticsResult`, `Signal`, `TradePlan`, `PortfolioState`, `RiskDecision`, `Order`, `ExecutionResult`, `ReconciliationReport`. All frozen, versioned, `correlation_id` traced.
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
pytest -q                          # all M1 tests
python scripts/check_structure.py  # bible §3 enforcement
# import-linter (after pip install)
lint-imports
```

## Build Independence (Airplane Test)

```bash
pytest tests/contract_tests --snapshot          # frozen schema
pytest tests/test_tenant_isolation.py -v       # isolation
pytest tests/test_clock.py -v                  # SimulatedClock deterministic
pytest tests/test_config.py -v                 # bad YAML → loud fail
# No DB, no broker WS, no M2-M12 — yet M1 can be tagged v0.1-foundation
```

## Next Phases

- **P05-P07** M2b Reference (instrument_master, symbol_mapping, contract_specs)
- **P08-P14** M2 Data (ingestion → storage)
- **P15-P21** M3 Analytics (M3.0 capability gate + 17 indicators)
- **P22-P26** M4 Alpha (11 families + M4.5 TradePlan)

See `System_Development_Manual/modules/` for per-module expanded discussions (2,550 lines).

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
