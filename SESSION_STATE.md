# SESSION STATE — resume here instantly after reboot
> Created: 2026-09-25. Read this FIRST, then `BUILD_STATUS.md`, then `USER_ACTIONS.md`.
> Purpose: survive reboot / new session. Delete or archive once acted on.

## What just happened (right before reboot)
- Human installed **WSL2 + Ubuntu** (phase 1 of infra setup). Reboot pending.
- Prepared files (unstaged, separate from the staged 27-file S0 batch):
  - `financial_analyst_app/infra/setup_hot_warm.ps1` — auto phase detection: if WSL/VMPlatform enabled → installs Docker Desktop silently (`install --quiet --accept-license`); else enables features + says reboot.
  - `financial_analyst_app/infra/docker-compose.yml` — `valkey/valkey:9.1` (HOT) + `timescale/timescaledb:2.30.1-pg17` (WARM), loopback-only ports 6379/5432, healthchecks, no secret literals (reads `TIMESCALE_DEV_PASSWORD` from `.env`).
  - `.env.example` — added empty `TIMESCALE_DEV_PASSWORD=`.
- Verification already run (all green): CI-pinned **gitleaks 8.30.1** worktree + targeted infra scan → **0 leaks**; `check_structure` OK; `tests/ci` 65/65; full suite **451 passed, 88.34% cov ≥85, exit 0** (`--ignore=tests/m3_analytics` as documented).

## Human decisions locked 2026-09-25 (question tool)
1. **Runtime path:** WSL2 → Docker Desktop → TimescaleDB (containers).
2. **G1 build order LGTM:** Redis/Valkey (HOT) → Timescale (WARM) → MinIO/S3 (COLD promotion **only if needed**). Infra install = env prep only; **application code stays gate-ordered (G0)** — no §1 amendment needed/used.
3. Earlier session decisions still standing: next code slices = **M2b.2 `current_snapshot_id()` spec → live-capture slice**; M2.1 legacy endpoint fix goes via **§5 change proposal (FROZEN, wait for sign-off)**; M3.0 Step 5 (A29) authorized-but-deferred behind those slices.

## Immediately after reboot — do this, in order
1. Verify features: run `financial_analyst_app/infra/setup_hot_warm.ps1` **elevated** → expect `[phase 2]` path → Docker Desktop installed silently.
2. Human starts Docker Desktop as normal user (not elevated); wait for engine running.
3. Human copies `.env.example` → `.env`, sets `TIMESCALE_DEV_PASSWORD` (dev-only value).
4. From repo root: `docker compose --env-file .env -f financial_analyst_app/infra/docker-compose.yml up -d --wait`
5. Verify: `docker ps` both healthy; `valkey-cli -h 127.0.0.1 ping` → PONG; `psql`/`pg_isready` connect → then record proof in `BUILD_STATUS.md` (Known Bugs entry "data layer has never held real data" stays OPEN until ticks actually flow).

## PENDING HUMAN ANSWER (ask on resume)
- **ACTION 29 status conflict:** both `USER_ACTIONS.md:10,14` and `BUILD_STATUS.md` still say *"ACTION 29 OPEN — LGTM on M3.0 TESTS"*, but my session notes claim the human said "lgtm" earlier. **Do NOT self-mark.** Ask: *"Confirm ACTION 29 = LGTM on M3.0 tests (Step 4) so I can mark it DONE?"*

## Next code slice after infra (serial, §2 order)
1. **M2b.2 slice:** spec `docs/specs/m2b.2_reference_snapshot.spec.md` — `reference.current_snapshot_id()` minting `REF-YYYY-MM-DD-NNN` from `config/reference/snapshot.yaml`; blocker: `modules/m2_market_data/normalize.py:96` calls it but `modules/reference/reference.py` lacks it (stubs only in `tests/m2_market_data/test_m2.2_spec.py:66` + `scripts/verify_m2.2_normalize.py:30`). Spec → tests → human LGTM → impl.
2. **Live-capture slice:** spec+tests first, then `scripts/verify_m2.1_live_capture.py` — capture real `frxEURUSD` ticks from the **working endpoint** `wss://api.derivws.com/trading/v1/options/ws/public` (tokenless; legacy `wss://ws.derivws.com/websockets/v3` in `config/brokers/deriv.yaml` = dead, HTTP 520) → raw JSONL + canonical parquet via M2.2→M2.8 → read-back assert.
3. **§5 proposal** for `deriv.yaml` `ws_url` fix — **SIGNED OFF + APPLIED 2026-09-25 (ACTION 41, human "alirght lgtm")**: ws_url → `wss://api.derivws.com/trading/v1/options/ws/public` (config-only; zero code readers; legacy `/websockets/v3` dead HTTP 520).

## Repo state at checkpoint
- Branch `master`, no remote. **27 files staged** (S0, 2575+/134−, includes proposed commit msg `S0 truth reconciliation: bible PIT-ban fixes + pit-guard docs scan (tests-first) + CI guard slice`); human owns commits.
- Unstaged: `infra/` (2 files) + `.env.example` edit + this file + `BUILD_STATUS.md` decision entries.
- Stale LSP false positive (known, tests pass): `tests/ci/test_check_pit_guard.py:6:48 "run_docs" unknown import symbol`.
- Verification commands: `python -m importlinter.cli` (not `lint-imports`); full green only with `--ignore=tests/m3_analytics`; local gitleaks at `$env:TEMP\gitleaks830\gitleaks.exe`.
