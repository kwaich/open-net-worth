# Acceptance criteria — Investments tab

Interaction-level GIVEN/WHEN/THEN for the **Investments** tab (Holdings,
Allocation, Performance, and Income sub-tabs).
The QA agent must exercise every criterion and report PASS/FAIL per ID.

### INV-LOAD
- GIVEN there is at least one investment position (equity, crypto, bond, etc.)
- WHEN I click "Investments" in the sidebar
- THEN the tab loads with the SummaryBar (Total Value, Today, Unrealized P&L,
  % of NW) and the "Holdings" sub-tab active — no JS error, no blank panel.

### INV-SUBTAB-SWITCH
- GIVEN I am on the Investments tab
- WHEN I click the "Allocation" segment in the sub-tab control
- THEN the Risk panel, Rebalance panel, and Portfolio X-ray panel are shown;
  the Holdings table is no longer visible.

### INV-GROUPBY-LENS
- GIVEN I am on the Holdings sub-tab with multiple positions
- WHEN I change the "Group by" control from "Asset class" to "Account"
- THEN the holdings table regroups by account name (group headers change to
  account names), and the per-group subtotals update accordingly.

### INV-SEARCH-FILTER
- GIVEN I am on the Holdings sub-tab
- WHEN I type a symbol or instrument name into the search field
- THEN only rows whose symbol/label/ticker match the query are shown; all
  other rows are hidden.

### INV-CLASS-FILTER
- GIVEN I am on the Holdings sub-tab with positions in multiple asset classes
- WHEN I click "Equity" (or any specific class) in the asset-class
  SegmentedControl
- THEN only holdings of that class are shown; the "All" option restores the
  full list.

### INV-SORT-COLUMN
- GIVEN I am on the Holdings sub-tab
- WHEN I click a column header (e.g. "Value")
- THEN rows within each section are sorted by that column descending; clicking
  the same header again reverses to ascending.

### INV-EXPAND-LOTS
- GIVEN a position has tax lots
- WHEN I click the expand (▸) affordance on a holding row
- THEN the lots detail panel expands beneath that row showing the lots
  (acquired date, qty, unit cost, etc.).

### INV-RECORD-SALE
- GIVEN I am on the Holdings sub-tab and at least one position exists
- WHEN I click "Sell" (or the disposal action) on a holding row
- THEN the disposal modal opens with the instrument name pre-filled and
  fields for quantity, proceeds, and date.

### INV-PERFORMANCE-PANEL
- GIVEN I am on the Investments tab
- WHEN I click the "Performance" sub-tab
- THEN the Returns panel is shown with TWR or XIRR figures for at least one
  period (1M / 3M / YTD / 1Y / ALL) — not all dashes.

### INV-INCOME-PANEL
- GIVEN I am on the Investments tab
- WHEN I click the "Income" sub-tab
- THEN the Income panel loads (showing income events or an empty state — no
  JS error, no broken layout).

### INV-EMPTY-HOLDINGS
- GIVEN no investment positions exist (fresh install or all holdings removed)
- WHEN I view the Holdings sub-tab
- THEN an "EmptyState" message is shown (e.g. "No holdings match your
  filters.") — not a blank white area or a thrown error.

---

## Phase 5 — ReturnsPanel chart + data contract (P2-INV3 Phase 5)

### INV-PERF-1W-PERIOD
- GIVEN I am on the Performance sub-tab
- WHEN I click the "1W" segment in the period control
- THEN the segment is labelled "1W" in the UI and the underlying API request
  is made with `period=7D` (not `period=1W`).

### INV-PERF-CHART-RENDERS
- GIVEN sufficient price history exists for the selected window
- WHEN the Performance sub-tab loads with the default 1Y period
- THEN a Chart.js line chart is rendered inside `.returns-chart-box` above the
  SummaryBar stat cards — not below them, and not inside a `.sumcard` wrapper.

### INV-PERF-BENCHMARK-CHART
- GIVEN I select a benchmark instrument in the benchmark picker
- WHEN the API returns a series with non-null benchmark values
- THEN a second dashed line (border-dash style) appears on the chart alongside
  the solid portfolio line; the benchmark symbol appears in the legend.

### INV-PERF-CLAMP-LABEL
- GIVEN the backend returns a clamped window (label starts with "Since…")
- WHEN the returns data is displayed
- THEN the "Since…" label appears as a trailing caption in the chart legend
  row (class `returns-chart-legend__since`); no separate EmptyState is shown —
  the chart and stat cards render normally.

### INV-PERF-ANNUALIZED-FLAG
- GIVEN the backend returns `annualized: true` for a long window
- WHEN stats are displayed
- THEN the TWR card label reads "TWR (ann.)" and the `twr.annualized` value
  is shown; if `annualized: false`, the label is plain "TWR" and
  `twr.period_return` is shown instead.

### INV-PERF-MWR-NULL
- GIVEN the backend returns `mwr: null` (windows under 1 year / < 360 days, where the
  annualized XIRR would be noisy/misleading — e.g. a 3-month window)
- WHEN stats are displayed
- THEN the MWR card is hidden entirely (not shown with "—", just absent). TWR and
  Period gain cards remain visible.
- WHEN the window is ≥ 1 year and `mwr` is non-null
- THEN the MWR card appears with label "MWR (your IRR)" and its annualized XIRR value.

### INV-PERF-UNRECORDED-RISK-NOTE
- GIVEN the response has `data_quality.has_unrecorded_risk: true`
- WHEN the performance figures are shown
- THEN a footnote containing "undated lots" and "understate risk" appears
  beneath the stat cards.

### INV-PERF-FX-APPROX-NOTE
- GIVEN the response has `data_quality.fx_approximate: true`
- WHEN the performance figures are shown
- THEN a footnote containing "today's rates" and "Multi-currency returns are
  approximate" appears beneath the stat cards.

### INV-PERF-PRIVACY-CHART
- GIVEN the hide-amounts privacy mode is active (body has class `hide-amounts`)
- WHEN the Performance chart is visible
- THEN the chart canvas remains visible and is NOT masked — the y-axis shows
  a dimensionless index (rebased to 100), never an absolute portfolio value,
  so no financial information is exposed through the chart.
