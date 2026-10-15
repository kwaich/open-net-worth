# Acceptance — Asset Detail: Chart types, OHLCV, volume

The asset-detail price chart now supports five chart types (Line, Mountain, Baseline, Candle, Bar) via a dropdown selector next to the existing timeframe control. A volume histogram pane renders below the price chart for all chart types. Hover tooltips display full OHLCV data (Date, Open, High, Low, Close, Volume). Candle and Bar chart types are disabled (with a visible reason on hover) when OHLC data is unavailable for the current asset and timeframe.

Context: `frontend/src/components/intel/AssetPriceChart.tsx` + `ChartTypeMenu.tsx`, using lightweight-charts library. Backend: `backend/services/asset_detail.py`, `backend/services/prices.py`.

## Chart type dropdown

### ACT-DROPDOWN-RENDERS
- GIVEN I open an asset detail page (any asset, any timeframe)
- WHEN the page loads
- THEN a **Chart type** dropdown appears in the header next to the timeframe selector, labeled with the current chart type (default "Line") and a dropdown icon

### ACT-DROPDOWN-LISTS
- GIVEN I click the Chart type dropdown
- WHEN the menu opens
- THEN it lists **exactly five options**: Line, Mountain, Baseline, Candle, Bar — each with an icon matching the chart shape

### ACT-SELECT-LINE
- GIVEN the dropdown is open
- WHEN I click **Line**
- THEN the chart re-renders as a line series (single line tracing close price), the dropdown closes, and "Line" is now the selected label

### ACT-SELECT-MOUNTAIN
- GIVEN the dropdown is open
- WHEN I click **Mountain**
- THEN the chart re-renders as an area (mountain) chart with a filled region under the line, the dropdown closes, and "Mountain" is now the selected label

### ACT-SELECT-BASELINE
- GIVEN the dropdown is open
- WHEN I click **Baseline**
- THEN the chart re-renders with a centered dashed baseline (reference = prior close price), the dropdown closes, and "Baseline" is now the selected label

### ACT-SELECT-CANDLE
- GIVEN the dropdown is open AND OHLC data is available for this timeframe/asset
- WHEN I click **Candle**
- THEN the chart re-renders as candlesticks (wicks show high/low, body shows open/close, colored by direction), the dropdown closes, and "Candle" is now the selected label

### ACT-SELECT-BAR
- GIVEN the dropdown is open AND OHLC data is available for this timeframe/asset
- WHEN I click **Bar**
- THEN the chart re-renders as an OHLC bar chart (vertical line from low to high, tick marks for open/close), the dropdown closes, and "Bar" is now the selected label

## Chart type persistence

### ACT-PERSIST-RELOAD
- GIVEN I select **Mountain** on an asset (e.g., AAPL on 1D)
- WHEN I reload the browser tab or navigate away and back to any asset detail page
- THEN the chart type defaults to **Mountain** on all subsequent asset detail opens (for this session, stored in localStorage)

### ACT-PERSIST-DIFFERENT-ASSET
- GIVEN I select **Candle** on asset A (AAPL)
- WHEN I navigate to asset B (MSFT) or use the asset search to open a different asset
- THEN asset B opens with **Candle** already selected (the chart type persists across assets)

### ACT-PERSIST-TIMEFRAME-SWITCH
- GIVEN I select **Mountain** on 1D for an asset
- WHEN I switch the timeframe to 5D, 1M, 1Y, 5Y, MAX, or back to 1D
- THEN the chart stays in **Mountain** view for each timeframe switch (chart type is independent of timeframe)

## Volume pane

### ACT-VOLUME-VISIBLE
- GIVEN I open an asset detail page in any chart type
- WHEN the page loads and the chart renders
- THEN a **volume histogram** pane appears directly below the price chart, showing volume bars for each candle/bar period

### ACT-VOLUME-COLORS
- GIVEN the volume pane is visible
- WHEN I view the volume bars
- THEN each bar is colored **red** if its close < open (bearish candle) and **green** if close ≥ open (bullish candle) — matching the price chart's direction

### ACT-VOLUME-ALL-TYPES
- GIVEN I switch between all five chart types (Line, Mountain, Baseline, Candle, Bar)
- WHEN I view each type
- THEN the volume pane remains visible and correctly scaled for every chart type — it is not hidden for any type

## Hover tooltip (OHLCV crosshair)

### ACT-TOOLTIP-HOVER
- GIVEN a chart is rendered for any asset in any chart type
- WHEN I hover over a candle/bar/data point on the chart
- THEN a **crosshair** line appears (vertical at the hovered date), and a tooltip appears showing the data for that point

### ACT-TOOLTIP-OHLCV
- GIVEN the tooltip is visible on a hover
- WHEN I read the tooltip content
- THEN it displays **Date, Open, High, Low, Close, Volume** — each field labeled and formatted with the appropriate number of decimals and currency/unit (e.g., "1.2M" for volume) — exactly matching the design mockup

### ACT-TOOLTIP-ALL-TYPES
- GIVEN I switch between all five chart types
- WHEN I hover over the chart in each type
- THEN the OHLCV tooltip appears consistently for every type, not just Candle/Bar — the data shown is always the full OHLCV set

## Graceful degradation (OHLC unavailable)

### ACT-OHLC-UNAVAILABLE-CANDLE-DISABLED
- GIVEN I open a **crypto asset** on a **long timeframe** (5Y or MAX) where the provider's OHLC history is too short (e.g., a new altcoin, or CoinGecko's free-tier range limit)
- WHEN I click the Chart type dropdown
- THEN the **Candle** option is **greyed out** (disabled, not clickable), and its row has a grey info icon

### ACT-OHLC-UNAVAILABLE-BAR-DISABLED
- GIVEN the same scenario (crypto on 5Y/MAX with no OHLC data)
- WHEN I click the Chart type dropdown
- THEN the **Bar** option is **greyed out** (disabled, not clickable), and its row has a grey info icon

### ACT-OHLC-UNAVAILABLE-TOOLTIP
- GIVEN Candle or Bar is disabled (greyed out)
- WHEN I hover over that disabled row
- THEN a tooltip appears with the reason: e.g., "OHLC data not available for this timeframe" or "Insufficient historical data"

### ACT-OHLC-UNAVAILABLE-FALLBACK-LINE
- GIVEN Candle/Bar are disabled
- WHEN the chart is already showing Candle (from a prior timeframe) and I switch to a timeframe where OHLC is unavailable
- THEN the chart automatically **falls back to Line** (close price only), and the dropdown shows "Line" selected

### ACT-OHLC-AVAILABLE-EQUITY
- GIVEN I open an **equity** (e.g., AAPL) on any timeframe including 5Y/MAX
- WHEN I click the Chart type dropdown
- THEN **Candle and Bar are enabled** (not greyed out) — equities always have OHLC data across all timeframes via yfinance

## Equity vs. Crypto rendering

### ACT-EQUITY-RENDERS
- GIVEN I open an **equity asset** (e.g., AAPL from the holdings or search)
- WHEN the asset detail page loads
- THEN the chart renders with crisp OHLC data at the selected timeframe (1D shows ~1-minute candles, 5Y shows weekly candles), and the dropdown shows all five chart types enabled (Candle/Bar not greyed out)

### ACT-CRYPTO-RENDERS
- GIVEN I open a **crypto asset** (e.g., BTC, ETH) on a **short timeframe** (1D, 5D, 1M) where a market-data provider returns real OHLC observations
- WHEN the asset detail page loads
- THEN the chart renders only those real provider observations with OHLC data and volume bars; supported major USD crypto uses Yahoo Finance first and CoinGecko is the fallback. The dropdown shows all five chart types enabled. An empty provider response must never be converted into a synthetic crypto line.

### ACT-CRYPTO-LONG-RANGE-DEGRADED
- GIVEN I open a crypto asset on 5Y or MAX
- WHEN the asset detail page loads and OHLC data fetch returns insufficient history
- THEN the chart defaults to **Line** (if selected chart type was Candle/Bar, it falls back to Line), Candle/Bar are greyed out in the dropdown, and the close-price line still renders (never errors)

## Edge cases & interactivity

### ACT-EMPTY-CHART-STATE
- GIVEN an asset detail page loads but price data fetch fails or returns empty
- WHEN the Chart type dropdown is visible
- THEN all five options are **either enabled or disabled uniformly** — the dropdown never shows a mix of enabled/disabled states (Candle/Bar are both greyed out together, or both enabled together)

### ACT-LIVE-PRICE-UPDATES
- GIVEN a 1D chart is open with live market data streaming (market hours)
- WHEN new trades/ticks arrive and the chart updates (intraday tail appended)
- THEN the selected chart type (e.g., Mountain) persists, the live tail is rendered in that type, and the volume pane continues to update — the chart type never reverts to Line

### ACT-RESPONSIVE-LAYOUT
- GIVEN I open an asset detail page on a **mobile or narrow viewport** (e.g., <600px width)
- WHEN the chart and dropdown render
- THEN the Chart type dropdown is still clickable and the chart is readable (not crushed), and the volume pane scales appropriately

### ACT-RESIZE-KEEPS-FULL-RANGE
- GIVEN a chart is rendered at any timeframe (e.g., 1D showing the full session)
- WHEN the chart pane resizes in EITHER direction — window drag, opening/closing the Lucius side panel, or split view
- THEN the full fitted time range stays visible edge-to-edge (bar spacing rescales): shrinking never drops the earliest bars off the left, growing never leaves dead space

### ACT-TOOLBAR-TYPE-RIGHT-ALIGNED
- GIVEN the chart toolbar renders at any pane width
- WHEN I look at the toolbar row
- THEN the timeframe control sits on the left and the Chart type dropdown is anchored flush to the toolbar's right edge

### ACT-TF-DROPDOWN-ON-NARROW-PANE
- GIVEN the chart PANE is narrower than ~640px (e.g., the Lucius side panel is open at a laptop viewport)
- WHEN the toolbar renders
- THEN the 8-option timeframe segmented control is replaced by a compact dropdown styled identically to the Chart type menu (never a native select, never a wrapped second row), and picking a timeframe from it re-fetches and redraws the chart

### ACT-FULLSCREEN-EXPAND
- GIVEN the inline chart is rendered with any timeframe/chart type selected
- WHEN I tap the expand button (toolbar right, next to the Chart type menu)
- THEN the chart opens in a near-fullscreen workspace modal with the SAME timeframe and chart type selected, volume pane and OHLCV tooltip included

### ACT-FULLSCREEN-INTERACTIONS
- GIVEN the fullscreen chart is open
- WHEN I drag horizontally / use the scroll wheel or pinch
- THEN the chart pans / zooms (the library's native interactions) — and the 60s live poll must NOT snap the viewport back while I'm panned/zoomed

### ACT-FULLSCREEN-TIMEFRAME-REFIT
- GIVEN I have panned/zoomed in fullscreen
- WHEN I switch the timeframe
- THEN the chart re-fits to show the new range in full (an old viewport over a new range is meaningless)

### ACT-FULLSCREEN-CLOSE
- GIVEN the fullscreen chart is open
- WHEN I press Escape or tap the modal's close button
- THEN the chart returns inline with my timeframe/chart-type selections retained, and the inline chart is back to the fitted, non-interactive read-out

### ACT-VOLUME-NO-LAST-VALUE-PILL
- GIVEN the volume pane renders for any asset
- WHEN the newest (in-progress) bar has little or no recorded volume yet
- THEN the volume pane shows NO last-value axis pill and no dashed baseline (a permanent "0" pill reads as a bug); per-bar volume remains available in the hover tooltip's Vol row

---

## Context notes

- **Instruments covered**: equity (via yfinance OHLCV), crypto (Yahoo Finance first for explicit supported-major USD mappings; CoinGecko fallback/search/long-tail)
- **Timeframes**: 1D, 5D, 1M, 6M, YTD, 1Y, 5Y, MAX
- **Backend flag**: `ohlc_available` is returned in `AssetPriceSeries` response; the frontend disables Candle/Bar when this flag is `false`
- **localStorage key**: chart type preference is stored globally (e.g., `asset-detail:chart-type`) and applied to all asset detail opens
- **No breaking changes**: Line chart (close-only) still renders correctly for any asset; OHLCV fields are optional on each data point
