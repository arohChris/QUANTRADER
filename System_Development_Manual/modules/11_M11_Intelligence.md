# M11 — INTELLIGENCE (AI Narrator — Read-Only) — EXPANDED DISCUSSION
### Explains, summarizes, retrieves. Never trades. Per-tenant context, guarded in code.

**Module Type:** Stateless per-request — read-only narrator, per-tenant.
**Owns State:** `llm_audit` (per-tenant logs) — no business state
**Input:** User prompt + read context **only for `user_id+broker_id` tenant** (from `M3` `AnalyticsResult`, `M5` `PortfolioState` per-tenant, `M9` `ResearchReport`)
**Output:** `Answer` (citations, provenance) — never `Order`

> **Expanded for M11** — each file simple + pseudocode + connection. This patches into master §25.

---

## 1. Purpose in One Sentence
> **M11 answers “Why did we short EURUSD?” by reading `M4` `explainability` for *that* tenant’s `Signal`, not by trading.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution <500ms, tick→parquet <50ms, analytics <20ms, risk <10ms. No calc/file/abnormality tolerated — async I/O + sync pure calc + event_bus + frozen contracts + golden tests + well-commented WHY.*

## 2. The 3 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M11.1** | Context & Prompts | Gather per-tenant context, versioned prompts | Change prompt |
| **M11.2** | Validation | Cross-check vs DB/API before answer | Change validator |
| **M11.3** | Guard & Audit | Block hallucinated trades, log | Change allowlist |

### `intelligence/context_builder.py`, `prompt_manager.py`, `source_registry.py`
**Does:** `context_builder` does `tenant_context.set(user_id, broker) → get PortfolioState(user_id,broker) + recent Signals(user_id,broker) + AnalyticsResult → build prompt`; `prompt_manager` holds versioned `prompts/summarize_portfolio_v2.txt` (no hard-coded strings); `source_registry` allowlists `internal DB read models, Lake, config` — not live broker.
**Pseudocode:**
```python
def build_context(prompt, user_id, broker):
    tenant_context.set(user_id, broker)  # per-tenant!
    portfolio = portfolio_risk.get_state(user_id=user_id, broker=broker)  # only his
    signals = alpha.get_signals(user_id=user_id, broker=broker, limit=5)
    return prompt_manager.render("explain_signal", {portfolio, signals, prompt})
```

### `factual_validator.py`, `calculation_validator.py`, `provenance.py`
**Does:** `factual_validator` checks `LLM says "RSI 70" → actually `AnalyticsResult.features["rsi_14"]==68? If mismatch → block or mark "unverified"`; `calculation_validator` recomputes `PnL = (mark - avg)*qty` via `M3/M7` instead of trusting LLM math; `provenance` attaches `source: AnalyticsResult id 123, timestamp`.
**Pseudocode:**
```python
def validate(answer, user_id, broker):
    claimed_rsi = extract(answer, "RSI")
    actual = analytics.get_features(symbol="EURUSD", user_id=user_id, broker=broker).features["rsi_14"]
    if abs(claimed_rsi - actual) > 1: answer = guard.block("hallucinated RSI")
    return answer
```

### `response_builder.py`, `response_guard.py`, `tool_policy.py`, `audit.py`
**Does:** `tool_policy` **hard-coded** `ALLOWED=["analytics.query","portfolio.query"]`, `BLOCKED=["order.*","execution.*"]` — cannot be enabled via config, requires code + bible change. `response_guard` blocks disallowed content (e.g., hallucinated trade). `audit` logs every `prompt + tool call + answer` per-tenant.
**Pseudocode:**
```python
ALLOWED = ["analytics.query","portfolio.query","market_data.query"]
BLOCKED = ["order.*","execution.*","risk.override"]
def call_tool(name, user_id, broker):
    if name in BLOCKED: audit.log("blocked", name, user_id, broker); raise GuardError
    if name not in ALLOWED: raise GuardError
    return tool_fn(tenant_context.set(user_id, broker))
```
**Connects:** `M10 Experience` chat panel → `M11` → reads `M3/M5/M9` per-tenant, never `M6`.

**Build Independence:**
```bash
pytest modules/intelligence --mock=PortfolioState --expect=Answer
# tool_policy test: attempt "order.create" → guard rejects + audit, per-tenant mocked
```



---

## Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Module | Sub-Module | Gate |
|-------|--------|------------|------|
| **P52** | M11.1 | Context & Prompts — `context_builder` (includes `TradePlan` entry/SL/TP + `M3.0` status) | `context` |
| **P53** | M11.2 | Validation — `factual_validator` (checks `TradePlan` RR/validity) | `factual` |
| **P54** | M11.3 | Guard & Audit — `tool_policy` (BLOCKS `order.*` — AI narrates `TradePlan`, never executes) | `BLOCKED` |

**Parallel after M3+M5 (incl. TradePlan):** `M11` narrates `TradePlan` (`why entry 1.085 SL 1.0835`) but cannot trade — `test_ai_cannot_trade` checks `TradePlan` not `Order`.

## 3. Open Questions for M11

- [ ] Should `M11` ever *suggest* params (`rsi 12→14`) as read-only suggestion (human approves), or strictly explain?
- [ ] LLM provider: OpenAI vs local — where key stored?
---
