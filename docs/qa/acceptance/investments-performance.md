# Acceptance criteria — Investments / Performance sub-tab (Wave 2)

Interaction-level GIVEN/WHEN/THEN for the enhanced **Performance** sub-tab: risk-adjusted
metrics grid (vol / beta / Sharpe / max drawdown) gated on data sufficiency, per-metric
tooltips, a curated SPY-canonical benchmark roster, a plain-English summary paragraph, and
responsive 4-up→2-up grid collapse at ≤ 680 px.

The QA agent must exercise every criterion and report PASS/FAIL per ID. Run against a QA
instance on ports 8090–8099 with `ONW_QA_MODE=1`. Seed at least 12 months of price history
for at least one position so the sufficiency gate can be exercised in both states.

---

## Risk-adjusted metrics grid — happy path

### INV-P-METRICS-GRID-SHOWS
- GIVEN the Performance sub-tab is active and the portfolio has ≥ 1 year of price history
- WHEN the sub-tab loads
- THEN a risk-adjusted metrics grid is visible below the returns chart, containing four
  metric cards: Volatility (annualized std dev), Beta (vs benchmark), Sharpe ratio, and
  Max Drawdown — all showing numeric values, none showing "–".

### INV-P-VOL-VALUE
- GIVEN the metrics grid is shown with sufficient history
- WHEN I read the Volatility card
- THEN it shows an annualized % value (e.g. "12.4%") with a label "Annualized volatility"
  (or equivalent); the value is consistent with the portfolio's daily return dispersion.

### INV-P-BETA-VALUE
- GIVEN the metrics grid is shown and a benchmark is selected
- WHEN I read the Beta card
- THEN it shows a dimensionless numeric (e.g. "0.87") with a label referencing the
  benchmark ticker (e.g. "Beta vs SPY"); the value is not "–" when sufficient history exists.

### INV-P-SHARPE-VALUE
- GIVEN the metrics grid is shown with sufficient history
- WHEN I read the Sharpe ratio card
- THEN it shows a dimensionless numeric (e.g. "1.23") with a label "Sharpe ratio"; positive
  values indicate return per unit of risk above the risk-free rate.

### INV-P-MAXDD-VALUE
- GIVEN the metrics grid is shown with sufficient history
- WHEN I read the Max Drawdown card
- THEN it shows a negative % value (e.g. "–18.2%") with a label "Max drawdown"; the value
  represents the largest peak-to-trough decline in the selected window.

---

## Data-sufficiency gate (no "–" wall)

### INV-P-METRICS-GATED-SHORT-HISTORY
- GIVEN the portfolio has fewer than 90 days of price history for all positions
  (insufficient for meaningful risk metrics)
- WHEN the Performance sub-tab loads
- THEN the risk-adjusted metrics grid is NOT shown as a row of "–" dashes; instead, the
  entire grid section shows a sufficiency notice (e.g. "Need at least 90 days of price
  history to compute risk metrics") — a clean explanation, not a broken-looking "–" wall.

### INV-P-METRICS-GATED-PARTIAL
- GIVEN some positions have sufficient history and others do not
- WHEN the Performance sub-tab loads
- THEN the metrics grid shows values computed from the positions with sufficient history,
  with a footnote indicating partial coverage (e.g. "Based on N of M positions"); no "–"
  appears in the primary metric values.

### INV-P-METRICS-PERIOD-GATE
- GIVEN sufficient overall history exists but the user selects a short period (e.g. 1W or 1M)
- WHEN the period is selected
- THEN the risk-adjusted metrics grid either refreshes to show period-appropriate values OR
  shows the sufficiency notice if the selected window is too short — it never shows a
  row of "–" for all four cards simultaneously without explanation.

---

## Per-metric tooltips

### INV-P-TOOLTIP-VOL
- GIVEN I am on the Performance sub-tab and the metrics grid is visible
- WHEN I hover (or tap on mobile) the info icon on the Volatility card
- THEN a tooltip appears explaining what annualized volatility means in plain English
  (e.g. "How much your portfolio's returns fluctuate annually — higher means more
  unpredictable returns"); the tooltip disappears when I move the cursor away.

### INV-P-TOOLTIP-BETA
- GIVEN I hover the info icon on the Beta card
- WHEN the tooltip appears
- THEN it explains Beta relative to the benchmark (e.g. "Sensitivity of your portfolio
  to benchmark moves — a Beta of 1.2 means your portfolio tends to move 20% more than
  the benchmark") in plain, non-jargon language.

### INV-P-TOOLTIP-SHARPE
- GIVEN I hover the info icon on the Sharpe ratio card
- WHEN the tooltip appears
- THEN it explains the risk-adjusted return concept (e.g. "Return earned per unit of
  risk taken, above the risk-free rate — higher is better"); the tooltip is readable
  (not truncated) within the viewport.

### INV-P-TOOLTIP-MAXDD
- GIVEN I hover the info icon on the Max Drawdown card
- WHEN the tooltip appears
- THEN it explains max drawdown in plain English (e.g. "Largest peak-to-trough decline
  in value during the selected period"); the tooltip is positioned so it does not overlap
  other metric cards.

### INV-P-TOOLTIP-ACCESSIBLE
- GIVEN I navigate the metrics grid using only the keyboard (Tab to the info icon, Enter/Space)
- WHEN I activate an info icon via keyboard
- THEN the tooltip appears and can be dismissed via Escape; the tooltip is reachable and
  readable without a mouse.

---

## Curated SPY-canonical benchmark roster

### INV-P-BENCHMARK-PICKER-OPENS
- GIVEN I am on the Performance sub-tab
- WHEN I click the benchmark picker / selector
- THEN a dropdown or panel opens showing a curated list of benchmark options.

### INV-P-BENCHMARK-ROSTER
- GIVEN the benchmark picker is open
- WHEN I view the list
- THEN the roster includes at minimum: SPY (S&P 500), QQQ (Nasdaq 100), VT (Global
  total market), and AGG (US Aggregate Bond) — a curated canonical set, NOT a free-text
  entry of arbitrary tickers.

### INV-P-BENCHMARK-SELECT-SPY
- GIVEN the benchmark picker is open
- WHEN I select "SPY"
- THEN the picker closes, the chart legend shows "SPY" as the benchmark line, and the
  Beta card label updates to "Beta vs SPY"; the benchmark line appears on the returns chart.

### INV-P-BENCHMARK-DESELECT
- GIVEN a benchmark (e.g. SPY) is selected
- WHEN I open the benchmark picker and select "None" (or deselect the active option)
- THEN the benchmark line disappears from the chart, the Beta card shows the sufficiency
  notice or is hidden (Beta is undefined without a benchmark), and the legend no longer
  shows the benchmark ticker.

### INV-P-BENCHMARK-CHANGE
- GIVEN SPY is selected as benchmark
- WHEN I switch to VT
- THEN the benchmark line on the chart updates to reflect VT's returns; the Beta card
  label updates to "Beta vs VT"; no stale SPY data lingers.

---

## Plain-English summary paragraph

### INV-P-SUMMARY-VISIBLE
- GIVEN the Performance sub-tab has loaded with data
- WHEN I view the sub-tab
- THEN a plain-English summary paragraph is visible (separate from metric cards), describing
  the portfolio's overall risk/return profile in natural language.

### INV-P-SUMMARY-CONTENT
- GIVEN the summary paragraph is shown
- WHEN I read it
- THEN it references actual metric values (e.g. mentions the Sharpe ratio and whether
  it is above/below 1, or the portfolio's volatility relative to the benchmark) — it is
  NOT a static placeholder or a generic description that ignores the actual numbers.

### INV-P-SUMMARY-UPDATES-WITH-PERIOD
- GIVEN the summary paragraph is shown for the default period (e.g. 1Y)
- WHEN I switch to a different period (e.g. 3M or ALL)
- THEN the summary paragraph re-renders to reflect the metrics for the new period; it
  does not remain frozen on the old period's description.

### INV-P-SUMMARY-HIDDEN-INSUFFICIENT
- GIVEN the risk metrics are gated (insufficient history)
- WHEN I view the Performance sub-tab
- THEN the summary paragraph is either hidden or replaced by the sufficiency notice —
  it does NOT show a paragraph that references metric values when those values are unavailable.

---

## Responsive 4-up → 2-up grid at ≤ 680 px

### INV-P-GRID-4UP-DESKTOP
- GIVEN the viewport is wider than 680 px
- WHEN I view the risk-adjusted metrics grid
- THEN all four metric cards are displayed in a single horizontal row (4-up layout);
  no card wraps to a second row.

### INV-P-GRID-2UP-NARROW
- GIVEN I resize the viewport to 680 px or narrower
- WHEN I view the risk-adjusted metrics grid
- THEN the four cards reflow to a 2-column × 2-row layout (2-up); all four cards are
  still visible and no card is hidden or clipped.

### INV-P-GRID-2UP-READABLE
- GIVEN the viewport is ≤ 680 px and the grid is in 2-up layout
- WHEN I read each card
- THEN the metric name, numeric value, and info icon are all fully visible within the
  card; no text is truncated or overflowed outside the card boundary.

### INV-P-CHART-RESPONSIVE
- GIVEN the viewport is ≤ 680 px
- WHEN I view the returns chart above the metrics grid
- THEN the chart resizes to fit the viewport width; the legend and axis labels remain
  legible; the chart does not overflow horizontally.

---

## Empty / loading / error states

### INV-P-EMPTY-NO-POSITIONS
- GIVEN no investment positions exist (fresh instance)
- WHEN I navigate to the Performance sub-tab
- THEN an EmptyState is shown (e.g. "Add positions to see performance metrics") — not a
  broken chart or a grid of "–" values, and no JS error.

### INV-P-LOADING
- GIVEN the Performance sub-tab is loading returns data
- WHEN the request is in-flight
- THEN a loading skeleton is shown in place of the chart and metrics grid — the sub-tab
  does not flash blank before populating.

### INV-P-ERROR
- GIVEN the backend returns a 5xx error for the returns endpoint
- WHEN the Performance sub-tab loads
- THEN an error state is shown with a Retry button; neither a partial chart nor metric
  cards with "–" are shown alongside the error.
