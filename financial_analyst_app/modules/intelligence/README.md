# M11 INTELLIGENCE — module README

> Bible: `System_Development_Manual/modules/11_M11_Intelligence.md` · Canonical tree §3 `modules/intelligence/`
> **Status: DEFERRED — zero code by design.** Folder created 2026-09-25 (human directive: group M10/M11 backend homes for future work). No spec, no tests — nothing may be implemented until a spec is written and LGTM'd (AGENTS §0).

## 1. Purpose
Read-only AI narrator: explains, summarizes, retrieves — per-tenant context, guarded in code. **Never trades.**

## 2. Ownership / State
Planned files (manual §2): `context_builder.py`, `prompt_manager.py`, `source_registry.py` · `factual_validator.py`, `calculation_validator.py`, `provenance.py` · `response_builder.py`, `response_guard.py`, `tool_policy.py`, `audit.py`. All `not started`.

## 3. Contracts consumed/produces
Consumes frozen contracts read-side only (`AnalyticsResult`, `Signal`, `PortfolioState`, M7 ledger views). Produces narrations with provenance — no new money contracts.

## 4. Dependencies
`shared` read-only. Enforced guard: import-linter contract **"Intelligence never trades"** — `shared` may never import `modules.execution`/`modules.trust` (so M11 cannot be wired into order paths through the kernel).

## 5. Why it is separate
Safety & delivery ring: independent cadence; read projections only; airplane-testable without the trading chain.

## 6. Deferred work
Everything. Entry gate: human opens M11 + LGTMs a spec (design captured in manual §2 sub-modules above).

## 7. Tests
None (by design — nothing exists).

## 8. Verify / run
N/A until first spec.

## 9. Open questions
Manual §3 lists them (context sources, tool policy scope, audit retention). Resolve at spec time, not before.
