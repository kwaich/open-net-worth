# 0024 — Unified investment-activity read-model: one signed feed, no new writer, Tax links out

- Status: Accepted
- Date: 2026-06-21
- Deciders: orchestrating agent (autonomous "drive E2E" mandate; user AFK and delegated the call)
- Related: design `docs/rfc/investments/sys-activity-aggregation.md`; ADR 0021 (hybrid IA — Activity is the new sub-tab, Tax stays a top-level main tab); ADR 0007 (assetNav doorways)

## Context

The hybrid IA (ADR 0021) adds an **Activity** sub-tab: one chronological, typed, SIGNED feed of everything that happened to the investment book — buys, sells, dividends/coupons/interest, vests, deposits/withdrawals, and trading fees — with a 30-day summary callout (count / net invested / income / fees) and a realized-G/L line. ADR 0021 already fixed the IA boundary: **Tax stays a top-level main tab** (shipped, ahead of spec), and the handoff's "tax touchpoint on Activity" is honored as a *link*, not a duplicate engine.

The implementation problem is that these facts live in five disjoint stores with five different read services and shapes, none mergeable (`docs/rfc/investments/sys-activity-aggregation.md` §Problem):

- **Buys** — `db/lot_store.py` (`LotStore`), read math `services/lots.py`; cost in `cost_currency`.
- **Sells (realized)** — `db/realized_event_store.py` (`RealizedEventStore`), read math `services/lots.py::realized_pnl`; proceeds in `proceeds_currency`, gain recomputed via `to_base`.
- **Income (dividend/coupon/interest)** — `db/income_event_store.py` (`IncomeEventStore`), read math `services/income.py::income_summary`; net = `gross − tax_withheld`, base via `income.event_base_amount`.
- **Vests** — `db/vest_event_store.py` (`VestEventStore`), base via `services/equity_report.py`.
- **Trading fees** — derived (not a store): `lot.fees` + `realized_event.disposal_fees`, tallied by `services/lots.py::fees_summary`.
- **Cash deposits/withdrawals** — `transactions.json` via `services/ledger.py::get_transactions`.

No service emits a **single signed, date-sorted, paginated** stream across these. Without one, each consumer (the Activity tab, Lucius, an export) would re-merge five shapes by hand and — the real hazard — re-derive base amounts, risking the exact "trust the stored `base_amount`" staleness bug the codebase is built to avoid. The decision is *how* to provide that merged feed without violating the architecture: does it get its own store/writer, and does it own any tax computation?

## Decision

A **read-only aggregation endpoint** — `GET /api/investments/activity` (+ `/summary`) backed by `backend/services/activity.py` — merging lots/realized/income/vests/fees/cash into one signed feed. Three load-bearing properties:

1. **One signed feed with base-recompute.** Each `ActivityRow` carries a NATIVE-currency `signed_amount` (sign applied at the boundary per a fixed table: buy/withdrawal/fee `−`, sell/dividend/coupon/interest/deposit/vest `+`; a `deferred` vest is `signed_amount=0` but still listed) AND a `base_amount` **RECOMPUTED** here via `core.fx.to_base` / `core.fx.txn_base_amount` / `income.event_base_amount` — never the stored snapshot. A single `rates` + `base_currency()` are loaded once and threaded through every per-source builder (one FX read for the whole feed, the `realized_pnl`/`income_summary` pattern). A regression test asserts that flipping the base currency shifts every `base_amount` — the keystone invariant.

2. **NO new store / writer / choke point.** The feed is *computed-not-stored*, derived entirely from existing stores read as-is, exactly like `services/returns.py` and `services/lots.py::realized_pnl`. It mutates nothing; all writes continue through the existing choke points (`ledger.create_transaction`, `apply_lot_change`, `apply_income_change`, `apply_equity_change`). Checkpoint-EXCLUDED by construction (no file written). No new idempotency surface — the only "idempotency" concern is **double-counting**, closed structurally and tested:
   - Dividends are sourced from `IncomeEventStore`, NOT `kind=income` txns; the cash lane excludes any txn already linked from an income event (`links.txn_id`).
   - A sale's `_maybe_deposit_proceeds` bridge leg is excluded (the cash lane skips `platform="Market"` synthetic-account counterparties), so a sell isn't also a deposit.
   - Acquisition fees are *already* amortized into `effective_unit_cost` and disposal fees *already* reduce proceeds, so buy/sell `signed_amount` is split on the **raw** price (`qty × unit_cost`, `qty × proceeds_per_unit`) and fees are emitted as **separate `fee` rows** — no line charges a fee twice (a tested invariant: `net_invested = Σraw_buy − Σraw_sell`, `fees_base` independent).
   - DRP books one `dividend` row (the income event) + one `buy` row (the DRP lot) — two economically real rows, no double cash.

3. **Tax stays a top-level tab; Activity *links out*, never recomputes tax.** The 30-day summary surfaces `realized_gl_base` — the *same* number `services/lots.py::realized_pnl` already computes for the window — purely so the callout can show it and deep-link to the Tax tab. `services/activity.py` calls **no** tax service (`services/tax*`); it honors the handoff's "tax touchpoint on Activity" as a link, exactly the boundary ADR 0021 set. Per-row instrument doorways reuse the KEPT mention system via `links.instrument_id` + `state/assetNav` (ADR 0007) — no new navigation surface.

`services/activity.py` is `services`-tier, pure (`db` stores + `core.fx` + sibling services, lazy where a cycle risks, the `income.py` pattern); `api/activity.py` is a thin router (validate the `type` CSV against the enum → one service call → shape), registered beside `income_router`/`lots_router`/`returns_router`. An optional `get_investment_activity` AI tool (headless read, tier T0) is a fast-follow, out of the Activity-tab MVP scope.

## Consequences

- **Positive:** every consumer (the Activity tab, Lucius, exports) gets one merged signed feed instead of re-merging five shapes by hand; the "never trust stored `base_amount`" invariant is satisfied once, centrally, by reusing the already-tested recompute helpers (`to_base`, `txn_base_amount`, `event_base_amount`); zero new write surface means zero new double-write/idempotency/checkpoint risk; the Tax boundary from ADR 0021 holds — no second tax engine, no recomputation drift.
- **Cost:** the real complexity is the **de-dup and sign correctness** (dividend-as-deposit, sale-bridge-as-deposit, fee-twice), each closed by an explicit structural rule and a dedicated regression test; the synthetic row `id` scheme (`"{type}:{source_id}"`) and the opening/undated-lot handling (`details.synthetic=true`, sorts first) are small but must be right. M effort (one ~250–300 LOC service of small builders, one thin router, one `useInvestmentActivity` hook, ~20 tests).
- **Performance:** a full scan of five small JSON stores with a single FX read — bounded by the same data sizes `realized_pnl`/`income_summary` already scan per call; pagination is applied after the merge (acceptable for a personal-finance dataset; an index is out of scope).
- **Additive / no migration:** two new GET endpoints; no schema change, no new file, no store change. `/api/realized-pnl`, `/api/income`, `/api/positions/{id}/lots`, `/api/returns` keep serving the other tabs unchanged.

## Alternatives considered

- **A persisted `activity_events` store written alongside every lot/income/vest change.** Rejected: it would duplicate authoritative data, add a sixth writer that must stay perfectly in sync with five choke points (a guaranteed-drift, double-write hazard), and re-introduce the stored-`base_amount` staleness bug on a base-currency change. A pure read view over the existing write-choke-pointed stores is strictly safer and is the established `realized_pnl`/`income_summary`/`fees_summary` posture.
- **Source dividends from `kind=income` transactions instead of `IncomeEventStore`.** Rejected: it would double-count cash dividends (once as income, once as a deposit) and diverge from how `income_summary` already reads events, not txns, to stay distinct from the ledger's total-income view.
- **Fold acquisition/disposal fees into buy/sell `signed_amount`.** Rejected: fees are already amortized into `effective_unit_cost` / netted from proceeds, so folding them again charges each fee twice; splitting on the raw price + separate `fee` rows keeps `net_invested` and `fees_base` independent and honest.
- **Compute realized G/L (or any tax figure) inside the Activity feed.** Rejected: violates the ADR 0021 IA boundary (Tax is a top-level tab; Activity is a touchpoint that *links*, not a duplicate engine). The summary surfaces the existing `realized_pnl` number for the callout and deep-links to Tax; it never calls a tax service.
- **Make each tab merge the five sources client-side.** Rejected: pushes base-recompute and de-dup logic into the frontend (and into every future consumer), the exact "monetary math must live in services" violation the architecture forbids.
