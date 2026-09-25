# USER_ACTIONS — WHAT YOU (HUMAN) MUST DO
# ALL CAPS. UPDATED CONSTANTLY. READ THIS FIRST.

## CURRENT STATUS (UPDATED 2026-09-25)
# GATE: G0 ENTRY
# M1 COMPLETE | M2b.1 COMPLETE | M2.1 COMPLETE | M2.6/2.7/2.8 COMPLETE
# M1.3 2-GATE COMPLETE | Frontend design FROZEN A16F
# ACTION 27 DONE — LGTM on M3.0 Capability spec (conditional: AGENTS.md critical)
# ACTION 28 DONE — LGTM on CI guard scripts
# ACTION 29 DONE — LGTM on M3.0 TESTS (Step 4) 2026-09-25 — Step 5 authorized
#   (was queued behind m2.10 §5 m2.11 — QUEUE REORDERED by you, see D3 below)
# ACTION 30 DONE — LGTM ON M2b.2 SPEC + TESTS (Step 4) 2026-09-25 ("lgtm") — impl written
# ACTION 31 DONE — LGTM ON M2b.2 IMPL (Step 7/8) 2026-09-25 — M2b.2 COMPLETE
# ACTION 32 DONE — LGTM ON M2.9 HOT SPEC + TESTS (Step 4) 2026-09-25 — 5 amendments applied
#   → Step 5 authorized (§12): hot.py + redis dep → verify on live Valkey → A33
# ACTION 33 DONE — IMPL + VERIFY LGTM ON M2.9 (Step 7/8) 2026-09-25 — human review
#   ("correct foundation — production hardening required" + "lgtm") + R2 follow-up →
#   ALL AMENDMENTS APPLIED → M2.9 COMPLETE (42 tests, verify 14/14, monotonic live-checked)
# ACTION 34 DONE — IMPL LGTM ON M3.0 CAPABILITY (Step 8) 2026-09-25 — human "lgtm":
#   impl modules/m3_analytics/capability/ + config/capabilities.yaml; tests 24/24;
#   suite 545/545; ruff 0; verify_m3.0_capability.py EXIT 0 ALL OK (P15 badge).
#   M3.0 COMPLETE → BUILD_STATUS row `complete`. NEXT: M3.1 Feature Engine spec
#   (D4 folder law + D5 Deriv Tier-1 scope baked in) → ACTION 35 = LGTM on spec.
# ACTION 35 DONE — LGTM ON M3.1 FEATURE ENGINE SPEC REV2 2026-09-25 ("lgtm"):
#   HUMAN LAW verbatim (Calculation TF Law + Depth + Isolation), Option-A resolver
#   seam, shared/timeframes.py (9 M2.4 strings parity-locked), §6a.4 provider port,
#   DataRequirement+history_duration, feat: cache ns, §8b vocab, symbol=instrument_id,
#   research pins (Wilder / flat→100 / BB pop ddof=0 / min-bars 15/15/28/34).
# ACTION 36 DONE — LGTM ON M3.1 TESTS (Step 4) 2026-09-25 — your change list
#   (PIT-vs-depth, demand-driven depth, planner union, §6a.4 broker parity)
#   received + applied to spec REV3 + tests (38) → Step 5 authorized, §12 7/7 logged.
# ACTION 37 DONE — IMPL + VERIFY LGTM ON M3.1 (Step 7/8) 2026-09-25 ("lgtm"):
#   engine/{analytics_registry,profiles,feature_engine}.py + errors.py +
#   shared/timeframes.py + analytics: blocks in 3 asset yamls; tests 38/38;
#   suite 572/572 0 skipped; ruff 0 slice+script; 5 guards OK; lint-imports 3/3;
#   verify_m3.1_feature_engine.py EXIT 0 ALL OK (real ticks → 1m candle + 15
#   probes + live Valkey miss→hit + P16 gate steady-state median <1ms).
#   M3.1 COMPLETE → BUILD_STATUS row `complete`. NEXT: Phase 2 = M3.2 SPEC
#   (analytics/ rsi_14 + ema_20 + tick_rate) → ACTION 38 = LGTM on M3.2 spec.
# ACTION 38/39 DONE — BLANKET LGTM ON M3.2 (you, 2026-09-25) —
#   human verbatim: "now i change the law for it now lgtm to put the respective
#   now and their spec for calc without making much noise and talking too much"
#   → covers A38 (spec LGTM incl. REV4 pins) + A39 (tests LGTM incl. the declared
#   m3.1 :289 amendment) + immediate Step 5. Order preserved: tests written
#   BEFORE code (honest collection RED captured), then impl, then Step 7 verify.
#   M3.2 COMPLETE same turn: tests 24/24; suite 596/596 0 skipped; ruff 0
#   slice+script; guards naming/pit/retention/build_order OK; lint-imports 3/3
#   KEPT; verify_m3.2_analytics.py EXIT 0 ALL OK (D4 discovery = 3 · goldens
#   exact flat100/loss0/EMA6.25/rate · 2-impl RSI 46 windows max_diff 2.84e-14
#   ≤1e-9 seed 20260925 from real close 1.08548 · real sample →
#   INSUFFICIENT:rsi_14 · e2e rsi+ema+tick_rate + PROXY `tick_count ≠ volume` ·
#   ADVISORY latency median 0.32ms < §4.1 20ms — reported not gated).
#   NEXT: Phase 3 — M4.1 Strategy Registry spec (P22 build order; the
#   `strategies/` files themselves = M4.2, gated on M4.1 + G0 one-strategy law;
#   remaining analytics = P18 batches, one slice at a time).
# ACTION 40 DONE — BLANKET LGTM ON ANALYSIS + STRATEGIES (you, 2026-09-25) —
#   human verbatim: "plesae fill the analysis and the strategies now lgtm"
#   (after: "thers nothin in the strategy yet") → covers BOTH slices end-to-end
#   (spec + tests + impl + verify + status), zero noise. Order preserved inside
#   each slice: spec → tests (honest collection RED captured) → impl → verify.
#   (a) M3.2 P18 per-asset analytics — 12 files (sma_20 std_20 zscore_20 returns
#       atr_14 adx_14 macd bb_percent donchian_20 choppiness vol_regime hurst);
#       spec docs/specs/m3.2_p18_per_asset.spec.md; tests 126/126; capabilities
#       +12 FULLY_SUPPORTED (additive); declared amendments applied (discovery
#       locks 3→15 at test_m3.2_spec.py:323 AND verify_m3.2_analytics.py:193);
#       verify_m3.2_p18.py EXIT 0 ALL OK — discovery 15 · 13 goldens exact ·
#       2-impl ATR 47 windows max_diff 8.13e-20 / ADX 33 windows 1.776e-14 ≤ 1e-9
#       (seed 20260925 from real close 1.08548) · e2e 12/12 FULLY_SUPPORTED ·
#       ADVISORY ≤0.74ms < §4.1 20ms (reported, not gated).
#   (b) M4.2 strategy files — 7 files (trend_following rsi_overbought_oversold
#       mean_reversion donchian_breakout pullback regime_based
#       microstructure_flow); spec docs/specs/m4.2_strategy_files.spec.md;
#       tests 27/27; contract PROVISIONAL until M4.1 re-LGTM (Open Questions);
#       capabilities strategies: +4 keys (trend_following mean_reversion pullback
#       regime_based) + donchian_breakout stub FULFILLED [donchian_20,
#       choppiness] (spec-declared; m3.0 anticipated it — no test asserted the
#       stub); verify_m4.2_strategies.py EXIT 0 ALL OK — 7 goldens exact ·
#       real-yaml manifest 7 keys · deriv eligibility 6 True / microstructure
#       False (reason "needs order_book_imbalance UNAVAILABLE") · ADVISORY
#       evaluate ≤0.005ms.
#   ALSO: 2 OUT recorded in spec (never silent) — cross_sectional_momentum +
#   statistical_arbitrage blocked on P19/M3.3 cross-asset (correlation/
#   hedge_ratio/stationarity). SEQUENCE FLAG: M4.2 built before M4.1 per this
#   directive (my prior A38 NEXT note superseded) — logged in BUILD_STATUS
#   Discrepancies; P15/P22 law holds (M4.1 still not started). Suite 749/749
#   0 skipped · ruff 0 on all new files · 5 guards OK · lint-imports 3/3 KEPT.
#   M3.2 P18 + M4.2 rows = `complete`. NEXT per queue: M4.1 Strategy Registry
#   spec (P22) — its Step 2 must re-LGTM/freeze the M4.2 contract — then G0
#   walk-forward (one strategy end-to-end).
# ACTION 38 PENDING (superseded by DONE above — original request kept for history) —
#   docs/specs/m3.2_analytics.spec.md — DRAFT 2026-09-25. First 3 D4 compute
#   files (rsi_14 Wilder / ema_20 Pine-seed / tick_rate mean) + test plan 24 +
#   verify plan. Adopts m3.1 research pins REV4 (corrected vs current TradingView +
#   TA-Lib docs per your verification table 2026-09-25 — TR attribution split,
#   RSI flat=100 relabeled project rule, min-bars per-number derivations; no fork). Declares the
#   m3.1 test:289 amendment (monkeypatch empty-package proof — zero prod code
#   change) which will be applied WITH the M3.2 tests and re-LGTM'd @A39.
#   Reply "lgtm" (or request changes) → Step 3 tests written against spec,
#   BEFORE any analytics/*.py file.
# ACTION 36 DONE — **LGTM ON M3.1 TESTS (Step 4)** 2026-09-25 (superseded header
#   below kept for history — change list received, applied, Step 5 ran, A37 done) —
#   tests/m3_analytics/test_m3.1_spec.py — 38 tests (REV3-amended), written against
#   spec BEFORE any impl code (§2 Step 3). ruff 0. Suite now 572/572 (impl landed).
#   3 TEST-DRIVEN AMENDMENTS to approve WITH the tests (REV2 silent on these):
#   M3.1-a REGIME_LABEL: provides_regime modules expose REGIME_LABEL(value)->str;
#          engine sets result.regime = REGIME_LABEL(computed_value).
#   M3.1-b register validates compute signature == (candles, params) at register
#          time (Isolation Law enforcement — smuggled provider arg → ValueError).
#   M3.1-c Profiles maps asset_class "commodity" → commodities.yaml (BUILD_STATUS
#          known-bug deferred to M3.1).
#   UPDATE 2026-09-25 (your directives): frontend tests MOVED to frontend/tests/;
#   shell scaffolded + npm typecheck/build green → frontend 19/19 (own runner),
#   backend 534/534 (m3.1 excluded, honest RED until Step 5). Hand-off doc:
#   frontend/HANDOFF.md. M3.1 A36 still awaits YOUR change list.
#   Reply "LGTM on tests for M3.1" (or request changes) → Step 5 impl (§12 gate first).
# ACTION 36F DONE — M10.4.0 SHELL OPENED + IMPLEMENTED (you, 2026-09-25, via plan approval):
#   tests LGTM + M10.4 opened + serial exception vs M3.1 granted; code ALL in frontend/:
#   package.json · tsconfig · vite.config.ts · index.html · app/{main,App}.tsx ·
#   app/mock_data.ts · shared/ui/ · frontend/{pytest.ini,ruff.toml,.gitignore,tests/};
#   npm install+typecheck+build GREEN · guards OK · frontend 19/19 · backend 534/534 ·
#   NO backend code in shipped frontend (decoupling tests enforce). NEXT FRONTEND:
#   M10.4.1 role-based shell (developer hand-off: frontend/HANDOFF.md).
# D3 QUEUE REORDER (you, 2026-09-25): "let move to analysis now that we have buffer to
#   test, we will come back to m2.10, just write everything it's supposed to do down and
#   mark it as deferred" → m2.10 WARM scope WRITTEN + marked DEFERRED
#   (docs/specs/m2.10_warm.spec.md). NEW ORDER: ANALYSIS (M3.0 Step 5 next, P15 law)
#   → m2.10 (resume) → §5 ws_url → m2.11 live e2e.
# ACTION 24 (M10.1) still PARKED — backend first per you.
# ARCHITECTURE LGTM 2026-09-25 — Analysis-layer design guidance captured at
#   docs/architecture_analysis_layer.md (§20 = M2.9 placement + cache-vs-truth law).

==================================================
ACTION 37 — IMPL + VERIFY LGTM ON M3.1 (Step 7/8) — DONE
==================================================
# STATUS: [DONE] 2026-09-25 — human "lgtm" after §7 report → Step 7/8 closed.
#   M3.1 marked `complete` in BUILD_STATUS (38/38, suite 572/572, verify EXIT 0).
#   WHAT WAS BUILT (Step 5, §12 gate 7/7 logged):
#     shared/timeframes.py — 9-entry INTERVAL_MINUTES, parity-locked to M2.4;
#     modules/m3_analytics/engine/errors.py — FeatureEngineError/DataError;
#     engine/analytics_registry.py — frozen DataRequirement + AnalyticsRegistry
#       (register validation: NAME/dup/compute-signature==(candles,params)/
#       REGIME_LABEL/dependency cycle DFS; execution_order topo; from_package);
#     engine/profiles.py — Profiles.from_dir (commodity→commodities.yaml map,
#       fail-loud, float normalize);
#     engine/feature_engine.py — FeatureRequest + capability interlock (PROXY
#       survivor warning) + registry filter + defaults<profile<overrides params
#       + feat: cache key (sha16 features/params, no user_id, TTL 60) fail-open
#       + planner union max(min+warmup, duration/tf) + wrong-series guard +
#       INSUFFICIENT/WARMUP warnings + regime + PROXY_LIMITED status + watermark;
#     config/assets/{forex,crypto,commodities}.yaml — analytics: rsi_14 {period 14}.
#   EVIDENCE (all GREEN 2026-09-25):
#     m3.1 tests 38/38 · full suite 572/572 (0 skipped) · ruff 0 slice+script
#     verify_m3.1_feature_engine.py EXIT 0 ALL OK — real sample ticks → M2.4
#       pipeline → 1 real 1m candle (O=1.0854 C=1.08548 n=2) + 15 probes
#       (capability interlock, PROXY "≠ volume", profile precedence 14.0,
#       dep-chain planner, 3× ValidationError, wrong-series, INSUFFICIENT,
#       WARMUP, dead-cache fail-open, live Valkey miss→hit 0 extra fetches,
#       feat: key/TTL, P16 gate median<1ms — samples printed)
#     guards: structure/naming/build_order/pit_guard/retention(84) all OK
#     lint-imports 3/3 KEPT (tool was declared in dev deps but missing — installed)
#   FLAGGED (your call, non-blocking): pre-existing ruff 0.16.8 debt in committed
#     files outside slice (test_m3.0_spec.py, verify_m2.4_candles.py — same
#     unused-noqa:E402 pattern; part of the known 184-error lint-debt item).
#   NEXT: Phase 2 — M3.2 spec (analytics/ rsi_14 + ema_20 + tick_rate per D4/D5;
#   will flag test_m3.1_spec.py:289 names()==() amendment when files land).

==================================================
ACTION 33 — IMPL + VERIFY LGTM ON M2.9 (Step 7/8) — DONE (conditional → applied)
==================================================
# STATUS: [DONE] 2026-09-25 — human review: "consistent... keep the core design" +
#   "correct foundation — production hardening still required" + "lgtm". EVERY
#   AMENDMENT APPLIED SAME TURN, verified:
#   1) MONOTONIC LAW (the "biggest thing") — spec §HUMAN LAW A33: never replace
#      newer cached state with older. set_latest gates on watermark (< reject / =
#      overwrite documented); set_forming gates on interval_start (older bucket
#      rejected, TypeError-safe). +1 GET per write (sub-ms, inside 50ms budget);
#      single-writer-per-instrument invariant documented; Lua CAS = stated future
#      hardening, no contract change. Tests: test_stale_watermark_cannot_overwrite_
#      newer (RED→GREEN), test_same_watermark_overwrites_not_ignores (documented),
#      test_stale_forming_bucket_cannot_overwrite_newer (symmetric).
#   2) A33 test suite: forming TTL (test_forming_ttl_applied) ✓,
#      read-side fallback symmetry (test_fallback_namespace_read_symmetric) ✓;
#      newer-replaces-older already covered by test_second_set_overwrites_latest
#      (wm1→wm2) — kept.
#   3) CANONICAL comment strengthened in hot.py header (M2.4 = canonical events;
#      durable finalized store = canonical history; M2.9 = acceleration only).
#   4) FORMING ownership law in spec (key = market context; payload = state for it;
#      Candle must never gain instrument identity).
#   5) CONSUMER law in spec: Bot reads AnalysisSnapshot ONLY (direct M2.9 reads
#      BANNED); analysis_mode CLOSED|LIVE_FORMING must be explicit strategy config,
#      never silent — binding on future strategy slices.
#   FINAL STATE: tests 41/41 (0 skipped); suite 520/520; verify_m2.9_hot.py EXIT 0
#   14/14 incl. "stale wm=1 rejected vs REAL Valkey"; ruff 0; all guards +
#   import-linter OK; §12 7/7 logged. BUILD_STATUS row -> complete (commit hash
#   pending — human owns commits).
#   A33 R2 (same-day follow-up: "materially better... one caveat... fix temporal
#   TypeError + A33 contract tests, lgtm") — ALL THREE APPLIED SAME TURN:
#   - TEMPORAL: naive/aware mismatch in set_forming comparison now REJECTS the
#     write + warning (invalid state is NOT permission to overwrite; key untouched;
#     self-heals at TTL). test_temporal_mismatch_rejects_write RED → GREEN.
#   - HARD INVARIANT: single-ordered-writer rule upgraded from implementation note
#     to HARD ARCHITECTURAL INVARIANT (human's verbatim text) in spec, hot.py
#     header, and architecture doc §21 — CAS/Lua MUST land before multi-writer.
#   - FORMAL TEST NAMES: test_stale_latest_watermark_rejected,
#     test_stale_forming_candle_interval_rejected; monotonic matrix complete
#     (newer replaces · equal overwrites · older rejected · forming same-bucket
#     · forming older rejected · temporal invalid rejected).
#   R2 FINAL: tests 42/42; suite 521/521; verify 14/14 live; ruff 0; guards OK.
# DESIGN DECISIONS RECEIVED 2026-09-25 (builder Q&A — they bind the m2.10 spec):
#   D1 BUFFER = FOLD FROM 1m RING ONLY — human: "this should be the standard lgtm by me"
#      — keep one 1m ring, derive ALL higher TFs from it; HARD-CODED but EXTENSIBLE
#      supported-TF registry the UI can request (chart display AND analysis — ONE
#      definition, none may conflict); trading consumes the same bars, passed to
#      analytics in time for analyzing a particular asset at a particular point in time.
#   D2 FLUSH = 4-HOUR COMFORT BUFFER, FIFO to Timescale — human: "it can hold
#      comfortably 4hr buffer data... whenever it completes 4hr the first initial
#      minute goes out to timescaledb like a FIFO queue" — ring = 240 x 1m bars;
#      each completed 4h window dequeues oldest minutes -> Timescale, FIFO order.
#      CONFIRMED 2026-09-25 (capacity question: builder measured 20 assets x 24h =
#      11.4 MiB possible; human answered "Keep 4h as decided"). BOUND CONSEQUENCE:
#      ring=240 cannot hold EMA200@1m pristine warmup (250 bars = 4h10m) nor folds
#      deeper than 4h — those warm up from Timescale (WARM), live-keep on ring
#      (architecture doc warmup-from-WARM + incremental live design). Instant from
#      ring: RSI/MACD/short-period folds up to 1h + forming bars for all TFs <= 4h.
#   D3 QUEUE REORDER (human verbatim, 2026-09-25): "let move to analysis now that we
#      have buffer to test we will come back to m10m [m2.10] just write everything is
#      supposed to do down and mark it as deferred" — ACTION TAKEN SAME TURN:
#      - docs/specs/m2.10_warm.spec.md written with EVERYTHING m2.10 will do (schema +
#        hypertable DDL, asyncpg pool, FIFO flush + flush-before-evict invariant,
#        durable cursor, hybrid ring+WARM warmup reads, redaction, edges, verify plan)
#        and banner-marked **DEFERRED**; BUILD_STATUS row = `spec` — DEFERRED.
#      - NEW QUEUE: ANALYSIS FIRST → back to m2.10 → §5 ws_url → m2.11.
#      - Next slice = M3.0 Capability Step 5 (tests already LGTM'd @A29; P15 build-order
#        law: M3.0 → M3.1 → M4.1 — analysis entry point).
#   D4 M3 FOLDER LAYOUT (human verbatim, 2026-09-25): "for analytics module i think we
#      should have a sub folder of all supported analytics and their requirements and
#      for all supported strategies and their requirements, each folder contains file
#      matching a relative[lly] different calculation for each" — SHAPE (to be encoded
#      in the M3.1 spec, Step 1 of next-next slice):
#        modules/m3_analytics/
#          capability/   # M3.0 availability gate — DONE (A34 pending)
#          analytics/    # ONE FILE per supported analytic = its calculation + its
#                        #   requirements/manifest (period, warmup, minimum_history, needs)
#                        #   e.g. rsi.py, ema.py, atr.py, adx.py, macd.py, bbands.py...
#          strategies/   # ONE FILE per supported strategy = its logic + its requirements
#                        #   e.g. trend_following.py, momentum.py, mean_reversion.py...
#      OPEN (flag at M4.2 spec, do not silently resolve): strategies/ placement —
#      inside m3_analytics per human D4 vs bible M4 Strategy Engine (04_M4_Alpha.md).
#      capabilities.yaml stays the broker-availability gate (A27/A29 frozen — unchanged);
#      per-file manifests carry the computation requirements when M3.1/M3.2 start.
#   D5 DERIV-FOCUS SCOPE LAW (human verbatim, 2026-09-25): "from the strategy lib we
#      have the strategies that deriv supports we should focus on those for now and the
#      analytics needed to make them work" — BINDS M3.1/M3.2+ scope and M4.2 order:
#      - IN SCOPE NOW: Tier 1 = 8 price-only FULLY_SUPPORTED families (bible
#        04_M4_Alpha.md §9): trend_following(EMA cross), momentum(RSI),
#        mean_reversion(zscore BBands), volatility_breakout(ATR), pullback,
#        cross_sectional_momentum, statistical_arbitrage/spread, regime_based —
#        PLUS their required analytics union: ema, sma, std, zscore, rsi_14, returns,
#        atr, vol_regime, adx, choppiness, macd, donchian, bb, correlation,
#        relative_strength, spread_builder, hedge_ratio, stationarity, market_regime
#        (library FULLY_SUPPORTED entries: MA cross, MACD, ADX, Donchian, RSI/Stoch,
#        BB, Range...). Exact union frozen when M3.1 spec derives it.
#      - TIER 2 (PROXY_LIMITED, later, must warn): tick_activity/exhaustion/
#        execution_features.
#      - TIER 3 (UNAVAILABLE → stays DISABLED via M3.0): order_book_imbalance, vpin,
#        book_slope, queue_dynamics, absorption, volume_profile, market_impact,
#        toxicity — already encoded in config/capabilities.yaml.
#      Aligns with G0 law: "M3 only indicators that strategy needs".

==================================================
ACTION 32 — LGTM ON M2.9 HOT SPEC + TESTS (Step 4) — DONE (conditional → applied)
==================================================
# STATUS: [DONE] 2026-09-25 — human: "yes, this M2.9 test specification fits the
#   architecture" + 5 amendments, ALL APPLIED same turn:
#   1) cache_available != market_state_valid — ping = cache health only; M2.9 down
#      never stops Analysis; Valkey never a hidden trading dependency (spec §law + test
#      test_cache_unavailable_never_propagates_to_caller).
#   2) forming = mutable (overwrites expected, test_forming_overwrites_mutable_state);
#      finalized = immutable → M2.4/WARM only, never stored in M2.9.
#   3) staleness METADATA only — timestamps/watermark pass through; age policy
#      (fresh/stale/invalid) belongs to the consuming bot, not M2.9.
#   4) ONE canonical namespace — resolve_source_namespace(md) = provider-or-broker,
#      key builders are the only key formation path (test_..._provider_priority).
#   5) Responsibilities DOES/Does-NOT scope lock added to spec.
#   Tests now 28 functions / 36 items; architecture doc §20 appended (same message).
#   FILE: financial_analyst_app/tests/m2_market_data/test_m2.9_spec.py
#   SPEC: docs/specs/m2.9_hot.spec.md
#   Reply already given — builder proceeds to Step 5 (§12 gate first).
#   Blocks: m2.10 WARM + §5 ws_url + m2.11 live e2e (G0 data-layer bug).
#   Reply: "LGTM on tests for M2.9" (or request changes) → builder writes impl (§12 first).

==================================================
ACTION 31 — IMPL LGTM + EYEBALL VERIFY ON M2b.2 (Step 7/8) — DONE
==================================================
# STATUS: [DONE] 2026-09-25 — human "Yes ... lgtm" after §7 report → Step 7/8 closed.
#   M2b.2 marked `complete` in BUILD_STATUS (tests 28/28, verify EXIT 0, suite 479/479).
#   WHAT WAS BUILT (Step 5, §12 gate passed):
#     modules/reference/reference.py (+61) — load_snapshot_id() NEVER raises
#       (broad except + warning log → None; normalize.py:96 frozen consumer safe),
#       current_snapshot_id() @lru_cache(1) → str|None (spec contract).
#     config/reference/snapshot.yaml — seeded REF-2026-09-25-001, effective_from
#       QUOTED (unquoted ISO in YAML loads as datetime — loader requires str).
#     verify_m2.2_normalize / m2.3_validate / m2.4_candles — stub
#       "REF-2026-09-22-VERIFY" REPLACED by real reference.current_snapshot_id().
#     scripts/verify_m2b.2_snapshot.py — new real-config verify (8 checks).
#   EVIDENCE (all GREEN 2026-09-25):
#     m2b.2 tests 28/28 · full suite 479/479 (m3_analytics excluded, queued)
#     verify_m2b.2_snapshot.py EXIT 0 (repo seed + 4 failure paths return None)
#     verify_m2.2/2.3/2.4 on data/sample_ticks_realistic.jsonl EXIT 0 each
#     naming/structure/pit/retention/build_order/import-linter ALL [OK]
#     ruff 0 errors on reference.py + test_m2b.2 + verify_m2b.2
#   CLOSED: human "Yes ... lgtm" 2026-09-25 → M2b.2 `complete`, m2.9 HOT opened next.

==================================================
ACTION 30 — LGTM ON M2b.2 SPEC + TESTS (Step 4) — DONE
==================================================
# STATUS: [DONE] 2026-09-25 — human "lgtm" → Step 4 closed → Step 5 impl written.
#   SPEC: docs/specs/m2b.2_reference_snapshot.spec.md
#   TESTS: financial_analyst_app/tests/m2b/test_m2b.2_spec.py — 28 tests
#     (14 edge + 6 parametrized edge + 3 adversarial + cache/golden/never-raises)
#   Honest RED at review time: 25 failed + 3 errors (impl absent, never skipped).
#   Blocks: real-tick normalization (normalize.py:96), hence m2.9/m2.10/m2.11.

==================================================
ACTION 29 — LGTM ON M3.0 TESTS (Step 4) — DONE
==================================================
# STATUS: [DONE] 2026-09-25 — human "lgtm" (session, question-tool confirm) → Step 4 closed.
#   Step 5 (implementation) now AUTHORIZED per AGENTS §12 (Critical Thinking Gate first),
#   but QUEUED behind the storage/pipeline slices the human LGTM'd 2026-09-25.
#   FILE: financial_analyst_app/tests/m3_analytics/test_m3.0_spec.py
#   24 tests = 20 spec §Test plan names exact + 4 edge extras (empty_requires,
#   override_requires_no_status, missing_version_or_features, malformed_yaml)
#   — AGENTS.md §2 Step 3: one test per stated edge in §Edges.
#   Written against docs/specs/m3.0_capability.spec.md only.
#   Collection RED until Step 5 (no modules/m3_analytics yet) — honest, expected.
#   Gate test name frozen: test_order_book_unavailable_on_deriv
#   Reply: "LGTM on tests for M3.0" (or request changes)
#   After LGTM: Builder may write implementation (§12 Critical Thinking Gate first).

==================================================
ACTION 28 — LGTM ON CI GUARD SCRIPTS + tests/ci — DONE
==================================================
# STATUS: [DONE] 2026-09-24 — human "all lgtm if agents.md is followed critically".
#   Scripts (financial_analyst_app/scripts/):
#     check_pit_guard.py   — no alpha lake bypass (exit 0 pre-P22)
#     check_retention.py   — no AnalyticsResult cold parquet
#     check_build_order.py — G0 before M6; M3.0 before M4.1
#     check_naming.py      — mX.Y specs / test_mX.Y_spec / verify_mX.Y
#   Tests: financial_analyst_app/tests/ci/ (31 passed)
#   Live: all four [OK] on current tree

==================================================
ACTION 27 — LGTM ON M3.0 CAPABILITY SPEC (P15) — DONE
==================================================
# STATUS: [DONE] 2026-09-24 — human "all lgtm if agents.md is followed critically"
#   → conditional LGTM on spec. Condition: AGENTS.md §0–§13 followed critically
#   (serial steps, hard stop at ACTION 29 for tests, §12 before impl).
#   SPEC: docs/specs/m3.0_capability.spec.md (DRAFT → LGTM'd)
#   Gate test name frozen: test_order_book_unavailable_on_deriv
#   Blocks: M3.1 Feature Engine, M3.2 RSI, M4.1 (build order P15 first)
#   Day-1: broker lookup only; user_id accepted but ignored; no DB/I/O in get()

==================================================
ACTION 26 — IMPL LGTM ON M2.8 STORAGE — DONE
==================================================
# STATUS: [DONE] 2026-09-23 — human LGTM with 3 fixes → all applied + suite green:
#   1) watermark rejects bool (True/False not int>=1)
#   2) catalog.jsonl rewrite atomic (tmp → os.replace) — still no parquet+catalog 2PC
#   3) all partitions validated (_entry_from_rows) before first filesystem write
#   + @needs_parquet on test_user_id_not_in_created_path (pyarrow path).
#   pytest 25/25 · full suite 386/386 · structure OK · verify EXIT 0.
#   BUILD_STATUS M2.8 → complete.

==================================================
ACTION 25 — LGTM ON M2.8 TESTS — DONE
==================================================
# STATUS: [DONE] 2026-09-23 — human LGTM + 2 improvements applied:
#   1) explicit rows_in_write_order assert in checksum test
#   2) multi-row same-partition P14 test (3 rows → 1 file)
#   partition_key freeze already exact in REV1.1 spec.
#   user_id tree walk fixed to relative paths (test name false positive).

==================================================
ACTION 24 — LGTM ON M10.1 SYNCHRONIZATION SPEC (PARKED)
==================================================
# STATUS: [OPEN / PARKED] — human “2.8 first”. SPEC: frontend/specs/m10.1_synchronization.spec.md

==================================================
ACTION 23 — LGTM ON M2.8 STORAGE SPEC — DONE
==================================================
# STATUS: [DONE] 2026-09-23 — human LGTM + 5 freezes + 2 nits → applied REV1.1.
#   Partition identity, no parquet+catalog 2PC, rows_in_write_order, order_book
#   ValueError, JSONL one entry/key, read_partition signature, path segment rules.

## DEMO vs LIVE — HOW TO SWITCH (human OR auto-bot) — UPDATED 2026-09-23
# FILE: financial_analyst_app/config/accounts.yaml
#   mode: demo | live   under each broker (default_mode: demo while G0/G1)
#   live.enabled: false → must set true to ARM real-money (fail loud otherwise)
# ENV override (no file edit):  ACCOUNT_MODE=demo  or  ACCOUNT_MODE=live
# PLACEHOLDERS for future insert (human or bot) — NAMES only, never secret values:
#   .env (copy .env.example → .env, never commit .env):
#     DERIV_DEMO_ACCOUNT_ID / DERIV_DEMO_API_TOKEN
#     DERIV_LIVE_ACCOUNT_ID / DERIV_LIVE_API_TOKEN   ← fill only when going live
#     MT5_* / HEADWAY_* same pattern
#   OR vault (M6 private): vault.store(user_id, broker, token) — api_token_vault_key
# Market-data (M2.1) needs NO account token — only platform app_id in brokers/deriv.yaml.
#
# FRONTEND (M10.4) — PROJECT ROOT; M10.4.0 shell IMPLEMENTED + HAND-OFF 2026-09-25:
#   QUANTRADER2/frontend/       — own folder at root, M10.4 layout, zero backend code
#   QUANTRADER2/frontend/tests/ — ALL frontend tests live here (moved 2026-09-25)
#     run: cd frontend && python -m pytest   ·   npm run typecheck / build
#   NOT inside financial_analyst_app. Backend never serves HTML. Developer hand-off:
#   frontend/HANDOFF.md
# API JSON Day-1: GET/POST http://127.0.0.1:8000/api/v1/account/mode (will gain auto field + /auto-trade after impl)

## WHERE CONFIG FILES LIVE
# Root: financial_analyst_app/config/
#   accounts.yaml               — DEMO vs LIVE mode + env/vault placeholder NAMES
#   brokers/deriv.yaml          — Deriv ws_url + platform app_id (M2.1 connection)
#   brokers/mt5.yaml, headway.yaml
#   environments/paper.yaml     — DEMO / fake-money overlay (use this for demo)
#   environments/{development,testing,canary,production}.yaml
#   assets/{forex,crypto,commodities}.yaml — instrument master (M2b.1)
#   risk/limits.yaml, system/system.yaml, system/feature_flags.yaml
#   strategies/trend_following.yaml, execution/routing.yaml
# Repo root: .env.example — copy to .env and fill placeholders (never commit .env)
# Secrets: never in YAML — env (.env) or vault DB (private tokens).

==================================================
ACTION 13 — DONE: LGTM ON M1.3 2-GATE SPEC (MODE + AUTO)
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → Step 1 closed.
#   Spec frozen: docs/specs/m1.3_account_mode_two_gate.spec.md
#   Builder proceeds to Step 3 tests only (no impl until ACTION 14).

==================================================
ACTION 14 — DONE: LGTM ON M1.3 2-GATE TESTS (Step 4)
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → Step 4 closed → builder Step 5 impl.
#   tests/test_account_mode_two_gate.py (30) frozen as review baseline.

==================================================
ACTION 15 — DONE: SMOKE + IMPL LGTM (M1.3 2-GATE)
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → M1.3 2-gate marked complete in BUILD_STATUS.
#   Spec A13 + tests A14 + impl A15 all closed.

==================================================
ACTION 16 — DONE: LGTM ON M2.6 DATA QUALITY SPEC
==================================================
# STATUS: [DONE] 2026-09-23 — "LGTM on spec for M2.6" + 4 minors applied:
#   (1) store unified (per-instance seen+prev_map; optional dedup_store inject)
#   (2) paths locked financial_analyst_app/modules|tests/m2_market_data/...
#   (3) provider = md.provider or md.broker in fingerprint key
#   (4) prev_map broker = md.broker or md.provider fallback
#   Spec: docs/specs/m2.6_data_quality.spec.md — ready for tests.

==================================================
ACTION 17 — DONE: LGTM ON M2.6 TESTS (19)
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → Step 5 quality.py written.

==================================================
ACTION 18 — DONE: LGTM ON M2.6 IMPL (quality.py)
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → suite + verify green → BUILD_STATUS complete.
#   Module: financial_analyst_app/modules/m2_market_data/quality.py
#   Tests: 19/19 | verify: scripts/verify_m2.6_quality.py

==================================================
ACTION 19 — DONE: LGTM ON M2.7 RECOVERY SPEC
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → tests written; recovery.py NOT yet.

==================================================
ACTION 20 — DONE: LGTM ON M2.7 TESTS (19)
==================================================
# STATUS: [DONE] 2026-09-23 — "LGTM on tests" → Step 5 recovery.py written.

==================================================
ACTION 21 — DONE then SUPERSEDED: initial impl LGTM then REVOKED
==================================================
# STATUS: [SUPERSEDED] 2026-09-23 — human review: architecture OK, correctness
#   blocked on to_interval collapsing disjoint gaps. REV2 applied.

==================================================
ACTION 22 — DONE: LGTM ON M2.7 REV2.1 (disjoint-gap fix)
==================================================
# STATUS: [DONE] 2026-09-23 — human "lgtm" → BUILD_STATUS M2.7 complete.
#   REV2.1 hardening: §9 three-run + run-boundary, §7 multi-interval (28 tests).
#   Suite 353/353; verify disjoint + three-run OK; structure OK.
#   Ownership frozen: M2.2 sole allocator; M2.7 observe-only.

==================================================
ACTION 16F — LGTM ON FRONTEND DESIGN SPEC (non-blocking design lock)
==================================================
# STATUS: [OPEN] 2026-09-23 — design bible only; does NOT start M10.4 build.
#   SPEC: `frontend/specs/frontend_spec.md`
#   Captures: cockpit shell, Cmd+K center + right audit, chart Lake proof,
#   TradePlan rec cards, terminal AI/news, MODE+AUTO breaker panel (maps A13
#   two-gate), GitOps config, 5 crisp rules, API touchpoints for account/status.
#   Reply "lgtm frontend spec" → freeze file. M10.4 build still deferred
#   until human opens it after current backend slice (ACTION 20+) clears.

==================================================
ACTION 12 — DONE: HUMAN TEST M2.1 CONNECTION (P08)
==================================================
# STATUS: [DONE] 2026-09-23 — "tested and all parts are working" → BUILD_STATUS real-human
#   verify updated; M2.1 Connection stays complete. No further config action for demo.

==================================================
ACTION 11 — DONE: LGTM ON M2.2 NORMALIZE TESTS (Step 4 gate)
==================================================
# STATUS: [DONE] 2026-09-22 — superseded path; M2.2 complete @ e75adac (ACTION history).

==================================================
ACTION 10 — DONE: LGTM ON M2.1 TICK-INGEST REV1 + Drop 1.4
==================================================
# STATUS: [DONE] 2026-09-22 — "lgtm" → Drop 1.4 frozen, M2.1 REV1 signed, suite 139/139.

==================================================
ACTION 9 — DONE: LGTM ON M2.2 SPEC + DROP 1.4 CONTRACT
==================================================
# STATUS: [DONE] 2026-09-22 — "lgtm" → Step 1+2 closed → Step 3 tests written.

==================================================
ACTION 8 — DONE: EYEBALL M2.1 VERIFY (3 real Deriv assets)
==================================================
# STATUS: [DONE] 2026-09-22 — you replied "ok" → BUILD_STATUS M2.1 = complete.
# Output confirmed: accepts=4 drops=6; frxEURUSD/cryBTCUSD/frxXAUUSD bids/asks/quotes/pips correct.

==================================================
ACTION 7 — DONE: LGTM ON M2.1 TESTS (Deriv WS format)
==================================================
# STATUS: [DONE] 2026-09-22 — "lgtm" → Step 4 closed → TickIngestor rewritten for Deriv frames.

==================================================
ACTION 6 — DONE: EYEBALL M2.1 (superseded — wrong sample format)
==================================================
# STATUS: [DONE/CANCELLED] 2026-09-22 — sample was incorrect; replaced by ACTION 7.

==================================================
ACTION 5 — DONE: LGTM ON M2.1 TICK INGEST TESTS
==================================================
# STATUS: [DONE] 2026-09-22 — "lgtm" → Step 4 closed → Step 5 impl written.

==================================================
ACTION 1 — DONE: LGTM ON P05 TESTS
==================================================
# STATUS: [DONE] 2026-09-22 — "LGTM" → Step 4 closed → Step 5 impl written.

==================================================
ACTION 3 — DONE: EYEBALL REAL-DATA VERIFY
==================================================
# STATUS: [DONE] 2026-09-22 — you replied "ok" → M2b.1 marked complete in BUILD_STATUS.

==================================================
ACTION 2 — DONE: GIT IDENTITY + COMMITS
==================================================
# STATUS: [DONE] 2026-09-22 — identity Christian; efb59ff (first) + a6908c2 (M2b.1 complete).

==================================================
ACTION 4 — PERIODIC: KEEP THIS FILE FRESH
==================================================
# AI UPDATES THIS FILE WHENEVER:
#   - A SPEC/TEST PAIR IS READY FOR YOUR LGTM
#   - GATE CHANGES (G0->G1 ETC)
#   - BUILD_STATUS.md KNOWN BUGS APPEAR
#   - A NEW BLOCKING HUMAN STEP IS ADDED
# IF AN ACTION ABOVE SAYS [DONE], AI HAS ALREADY CLEARED IT.

==================================================
DO NOT
==================================================
# DO NOT TELL AI TO "SKIP GUARDRAILS" OR "MOVE FAST" ON
# RISK / EXECUTION / LEDGER — AI MUST REFUSE PER AGENTS.MD §10
# DO NOT MARK BUILD_STATUS "complete" WITHOUT LOG + HAND-CALC + LGTM
