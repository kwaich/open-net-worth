# 0023 — Risk-adjusted metrics engine: one owner, canonical-SPY beta, √365 calendar annualization

- Status: Accepted
- Date: 2026-06-21
- Deciders: orchestrating agent (autonomous "drive E2E" mandate; user AFK and delegated the call)
- Related: design `docs/rfc/investments/sys-risk-metrics-engine.md`; `docs/rfc/investments/sys-daily-total-value-series.md` (the series feed — its sketched `risk_metrics()` is SUPERSEDED here); `docs/rfc/investments/02-performance.md` (the UI consumer); ADR 0021 (hybrid IA — Performance sub-tab)

## Context

The Performance sub-tab wants one genuinely-new headline the redesign rejected the prototype for faking: a **risk-adjusted metrics block** — Volatility · Beta vs SPY · Sharpe · Max drawdown — plus a curated benchmark roster. The shipped prototype computed those from an **asset-class composition heuristic** (`riskStats`, a weighted guess off the allocation mix); the handoff explicitly rejects that heuristic and demands REAL math over a REAL daily total-return vector. The substrate already exists: `portfolio_history.portfolio_value_series` (`backend/services/portfolio_history.py:247`, reconstructed daily quantities) plus the new daily total-return series feed that overlays reinvested income.

**The decision was forced by an ownership collision between two Wave 2 system docs.** The daily-total-value-series feed (`sys-daily-total-value-series.md`) — whose actual job is the masthead sparkline and the daily-return *vector* — *also* sketched a `daily_total_value.risk_metrics()` function and a `GET /api/portfolio/risk` endpoint, computing vol/Sharpe/maxDD/beta itself. Independently, the risk-metrics-engine doc (`sys-risk-metrics-engine.md`) specified a dedicated `services/risk_metrics.py` with the same four numbers, a canonical-SPY beta anchor, and a curated roster. Left unreconciled, the codebase would ship **two risk engines and two risk endpoints** computing overlapping numbers from the same vector — guaranteed drift (a √252 in one, a √365 in the other; SPY-anchored beta in one, picker-anchored in the other), exactly the kind of duplicate-source bug the "single phrasing source / single benchmark source" invariants exist to prevent.

Two sub-questions also sat unresolved across the docs:

- **Beta anchor.** The shipped benchmark picker (`returns._resolve_benchmark_instrument`, `backend/services/returns.py:484`) resolves *any* instrument — fine as a *visual* chart overlay, wrong as a *Beta denominator*, which must be a fixed market reference.
- **Annualization factor.** Both docs flagged √252 (trading days) vs √365 (calendar) as an open question, deferring it to "decide against the feed's actual day-spacing." The series feed carry-forward-fills weekends/holidays, so the vector is **calendar-daily** (one point per day), not trading-daily.

## Decision

**Risk metrics live ONLY in `backend/services/risk_metrics.py`.** This module is the single owner of vol/beta/Sharpe/max-drawdown, the curated roster, and the deterministic plain-English one-liner. The series-feed slice's `risk_metrics()` function and its `GET /api/portfolio/risk` route are **dropped from scope** — the collision is resolved in this engine's favour because risk-adjusted metrics (the canonical anchor, the roster, the summary phrasing) are a coherent concern of their own, not a side-effect of the sparkline feed. Ownership is split cleanly:

1. **The daily-total-value series feed owns ONLY the series/sparkline** — `total_value_series` (the `daily_return` vector + the total-return cumulative path it produces) and `sparkline_series` for the masthead. It is the *substrate*; it computes no risk statistic.
2. **`services/risk_metrics.py` owns every risk number** — it *consumes* the feed's `daily_return` vector and TR cumulative path and assembles `portfolio_risk(period, benchmark)`. A degraded price-only fallback (deriving the vector itself via `portfolio_history.portfolio_value_series`, flagged `income_overlay=false`) keeps it shippable if the feeds land out of order.

3. **Additive on the shipped returns router — no parallel route.** The engine attaches to `GET /api/returns?...&risk=true` (so the Performance tab makes ONE request for chart + TWR/MWR + risk) plus a curated `GET /api/returns/benchmarks` roster endpoint. `risk` is an additive, default-off query param — existing `/api/returns` callers are byte-for-byte unaffected. **No `/api/portfolio/risk` route** (the series-feed sketch is superseded).

4. **Canonical-SPY beta.** Beta is **always** `cov(port_daily, spy_daily) / var(spy_daily)` over the date-aligned intersection, anchored to `CANONICAL_BETA_SYMBOL = "SPY"` — NEVER the user's chosen *visual* benchmark. Selecting BTC or "none" on the chart changes the overlay, not the Beta denominator. `null` (never fabricated) when SPY can't be anchored, the aligned window is too short, or `var(spy_daily) == 0`. A single server-authoritative `BENCHMARK_ROSTER = {SPY, VWRA, QQQ, ACWI, BTC, ""}` lives once in the engine and is exposed read-only so the FE never hard-codes a duplicate list.

5. **√365 calendar-day annualization.** The series vector is calendar-daily (carry-forward-filled weekends/holidays, one point per day), so volatility and the Sharpe vol term annualize on **√365**, the factor matching the series grain — resolving the √252-vs-√365 open question against the feed's *actual* day-spacing rather than the trading-day convention. `ANNUALIZATION_FACTOR` is a single module constant, so the choice is one line if the feed's grain ever changes.

6. **Sample variance.** σ and the beta covariance/variance use **sample** statistics (n−1 denominator) — the unbiased estimator appropriate for a finite return-sample window, fixed once in the engine's helpers.

7. **Bond damping is pure series-derived — zero asset-class constants.** A bond-heavy book moves less day-to-day, so `stdev(daily_return)` is naturally small and `cov(port, spy)` naturally low; the handoff's vol≈5%/beta≈0.15 *falls out* of the series (computed-mark bonds are excluded from `_MARKET_BANDS` and contribute a near-flat line). A per-asset-class constant would re-introduce exactly the composition heuristic the handoff rejected. The only tunables are `RISK_FREE_RATE`, `ANNUALIZATION_FACTOR`, and the roster — none asset-class-keyed.

`risk_metrics.py` is a `returns`-sibling, import-pure (`services → services/db/core`, no `api`/`ai`), with sibling imports lazy/in-function to avoid cycles. The deterministic `risk_summary` one-liner is the SINGLE place risk wording lives (chat/agents reuse it). A one-line `_subperiod_return` extraction in `returns.py` (a pure refactor guarded by `test_char_ledger_math.py`) gives the daily-return vector and the TWR chain a single code path so risk and TWR can never diverge.

## Consequences

- **Positive:** exactly ONE risk engine and ONE risk endpoint surface — no drift between two implementations of the same four numbers; Beta is meaningful (a fixed market anchor) regardless of the visual benchmark; the roster has a single server source; the calendar-grain annualization is matched to the data, not assumed; bond damping is honest (emergent, not faked). The Performance tab gets risk in the same payload as TWR/MWR (one round-trip).
- **Cost / coordination (recorded):** the series-feed slice MUST drop its `risk_metrics()` function and `GET /api/portfolio/risk` route — the orchestrator carries this hand-off so the two slices don't both ship a risk engine. If the slices land out of order, the engine's degraded price-only fallback (`income_overlay=false`) ships unblocked.
- **Documented limitations (intentional):** Sharpe over a <360d span uses a cumulative return over an annualized vol (a basis mismatch) — flagged "(short window)" in the summary rather than hidden; if no SPY instrument exists in the user's book, Beta degrades to `null` (`spy_anchored=false`) with an "add SPY to enable Beta" UI hint rather than synthesizing data. The risk-free rate is a module constant for v1 (promotion to `profile.json` is mechanical and deferred).
- **No new store, no migration, no checkpoint surface, no write choke point** — computed-not-stored, the `returns.py`/`networth_history.py` posture; every figure recomputes per request from the existing write-choke-pointed stores, read-only. No money movement / auth / external I/O → no `/security-review` trigger. Null discipline throughout: every metric is `null` (never a fabricated number) when its window/anchor is insufficient.

## Alternatives considered

- **Let the series feed own risk (keep its `risk_metrics()` + `/api/portfolio/risk`).** Rejected: risk-adjusted metrics are a coherent concern (canonical anchor + roster + deterministic phrasing), not a by-product of the sparkline substrate; co-locating them with the feed would have left the dedicated engine's roster/beta-anchor work homeless and invited two divergent computations.
- **Ship both and reconcile later.** Rejected outright: two engines computing the same four numbers from the same vector is the duplicate-source drift the codebase's single-source invariants exist to forbid — a √252-vs-√365 or anchor mismatch between them is a *when*, not an *if*.
- **Anchor Beta to the user's chosen visual benchmark.** Rejected: Beta vs an arbitrary picked line (BTC, "none") is meaningless; the market reference must be fixed (SPY) and independent of the chart overlay.
- **√252 (trading-day) annualization.** Rejected for this feed: the series is carry-forward calendar-daily (365 points/yr), so √365 is the factor that matches the actual day-spacing; using √252 over a calendar-daily vector would systematically misstate the headline vol magnitude.
- **A new `/api/portfolio/risk` route parallel to `/api/returns`.** Rejected: forces the Performance tab into a second round-trip and a second source; the additive `risk=true` param on the shipped returns route keeps one request and one contract.
- **Per-asset-class vol/beta constants to "produce" bond damping.** Rejected: that is precisely the composition heuristic the handoff threw out; damping must emerge from the real series.
