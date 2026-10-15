# Acceptance criteria — Income tab redesign + dividend calendar

Interaction-level GIVEN/WHEN/THEN for the redesigned Income tab (Investments › Income) and the new
dividend **Upcoming payments** calendar. The QA agent must exercise every criterion and report
PASS/FAIL per ID. `INC-CAL-SCHED-VS-EST`, `INC-CAL-EXCLUDE-NONPAYER`, and `INC-FWD-YIELD-MV` are the
ones that catch the high-value regressions (fabricated precision, non-payers shown, the misleading
cost-based yield).

## Environment notes

- Run against a QA instance on ports **8090–8099** with `ONW_QA_MODE=1`. **Never** drive `:5174` / `:8080`.
- All examples use synthetic data. Dividend metadata is **cached on the instrument** — tests seed
  `next_ex_date` / `last_ex_date` / `last_dividend_amount` / `dividend_frequency` directly (no network).
- Verify computed values via the API (`GET /api/income`, `GET /api/income/forecast`,
  `GET /api/income/calendar?days=90`) — do not rely on the UI alone.
- "today" in examples = 2026-06-21.

---

### INC-CARDS
- GIVEN a portfolio with recorded income and dividend-paying holdings
- WHEN I open Investments › Income
- THEN I see 5 stat cards: **Received · last 12m** (= `trailing_12m_base`), **Received · all time**
  (= `total_base`), **Yield on cost** (= `yield_on_cost_pct`), **Forecast · next 12m**
  (= `annual_total_base`), **Forward yield** (= `forward_yield_pct`, the **market-value** yield) —
  each matching the API to ±0.01 / ±0.1%.

### INC-WHERE-FROM
- GIVEN ≥1 holding with a forward income estimate
- WHEN I view "Where your income comes from"
- THEN holdings are listed **ranked by forecast contribution** (desc), each with symbol + name, a
  proportional bar, the base-currency forecast amount, and a **source dot** (green = declared,
  grey = trailing); the right-meta reads "N holdings · forecast next 12m"; non-paying / crypto
  holdings are absent.

### INC-GROWING
- GIVEN trailing and forecast income both exist
- WHEN I view "Is your income growing?"
- THEN I see a "Received · last 12m" bar and a "Forecast · next 12m" bar, a delta badge =
  `(annual_total_base − trailing_12m_base) / trailing_12m_base` (signed %), and a "How we estimate"
  legend with **Declared rate** (green) and **Trailing 12M** (grey) definitions.

### INC-FWD-YIELD-MV
- GIVEN holdings with large unrealized gains (market value ≫ cost basis)
- WHEN I read the Forward yield card and `GET /api/income/forecast`
- THEN `forward_yield_pct` = `annual_total_base / market_value_base × 100` (market value, not cost),
  `market_value_base` is present, and the legacy `implied_forward_yield_pct` (cost-based) is still
  returned but is NOT what the card shows.

### INC-CAL-SCHED-VS-EST
- GIVEN holding A with `next_ex_date` = 2026-08-20 (future, Yahoo-declared) and holding B with no
  `next_ex_date` but `last_ex_date` = 2026-03-23 + `dividend_frequency` = quarterly
- WHEN I view "Upcoming payments" / `GET /api/income/calendar`
- THEN A is `status="scheduled"` using its exact ex/pay dates (green dot), and B is
  `status="estimated"` with a **projected** ex-date ≈ `last_ex_date + 91d` rolled past today
  (2026-06-22) and pay-date = ex + 14d (grey dot).

### INC-CAL-HORIZON
- GIVEN payments projected inside and outside a 90-day window
- WHEN I request `days=90`
- THEN only payments with `today ≤ ex_date ≤ today+90` appear, sorted by ex-date ascending; the
  header reads "next 90 days · {count} expected · {total_base}"; `days` is clamped to 1–365.

### INC-CAL-NEXT-HERO
- GIVEN ≥1 upcoming payment
- WHEN I view the calendar
- THEN the hero shows the **soonest** payment: symbol, "in {N} days", "Own it through {ex_date} to
  receive this payout", the amount, and "pays {pay_date}"; rows ≤ 7 days out show an "in Nd" badge.

### INC-CAL-EXCLUDE-NONPAYER
- GIVEN a non-paying holding (e.g. a gold trust: no `dividend_rate_annual`, no `last_ex_date`, empty
  history)
- WHEN I view the calendar and "Where your income comes from"
- THEN that holding appears in **neither** — it is excluded, never shown with a fabricated date or
  amount.

### INC-CAL-AMOUNT-FX
- GIVEN a USD-quoted holding with `last_dividend_amount` 0.91 and 100 shares, base = SGD, USD→SGD 1.35
- WHEN I read its calendar row
- THEN `per_share` = 0.91 (quote ccy), `amount_base` = round(0.91 × 100 × qpu × 1.35, 2), and the row
  currency reflects the quote currency.

### INC-EMPTY
- GIVEN a portfolio with no income and no dividend-paying holdings
- WHEN I open the Income tab
- THEN an empty state explains income/forecast/calendar will appear once a dividend is recorded or a
  paying instrument is held — no crash, no NaN, no div-by-zero.

### INC-REFRESH
- GIVEN the Income tab is open
- WHEN I record a dividend via the Holdings "Dividend" action
- THEN the panel refetches (version bump) and Received · last 12m / all-time update without a full
  page reload.

### INC-STATES
- GIVEN the income/forecast/calendar endpoints are loading or erroring
- THEN the panel shows the Loading and ErrorState primitives respectively (never a blank panel or a
  half-rendered card).
