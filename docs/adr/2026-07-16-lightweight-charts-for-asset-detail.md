---
title: Adopt lightweight-charts for the asset-detail price chart (Chart.js kept elsewhere)
status: Accepted
date: 2026-07-16
deciders: orchestrating agent (Claude) + repo owner (chart-types feature, 2026-07-16)
---

# Adopt lightweight-charts for the asset-detail price chart

## Context

The owner asked to support Yahoo Finance-style chart types (Line, Candle, Baseline,
Mountain, Bar) plus a volume subplot and OHLCV crosshair tooltip on the asset-detail price
chart (`frontend/src/components/intel/AssetPriceChart.tsx`). That component currently
renders a single Chart.js `line` dataset off close-price-only data; Chart.js is the only
charting dependency in `package.json` (no Recharts/visx/lightweight-charts/d3/echarts),
and no candlestick/OHLC/bar primitive exists anywhere in the codebase.

Two realistic options: extend Chart.js with the `chartjs-chart-financial` plugin, or
adopt a purpose-built financial charting library for this one component.

## Decision

Adopt **`lightweight-charts`** (TradingView's open-source library) as a new frontend
dependency, scoped to `AssetPriceChart.tsx` only. Its series vocabulary — Line, Area
(Mountain), Baseline, Candlestick, Bar, and Histogram (volume) — maps directly onto the
five requested chart types plus the volume pane, with built-in crosshair support. Chart.js
remains the library for every other chart in the app (portfolio sparkline, etc.) — this is
not a global charting migration.

## Consequences

### Positive
- All five chart types + volume + crosshair/tooltip map onto first-class library
  concepts instead of custom Chart.js glue — significantly less bespoke rendering code.
- Purpose-built for exactly this use case (OHLC financial time series); actively
  maintained, used widely in production finance UIs.
- Isolated blast radius: one component's rendering layer changes; the fetch/cache/
  live-tail logic and the component's external props are unchanged.

### Negative
- A second charting library in the frontend dependency tree (alongside Chart.js) —
  slightly more bundle weight and one more library's API surface for contributors to
  learn.
- If a future chart elsewhere in the app wants candlestick/OHLC rendering, there's now a
  choice between two libraries rather than one obvious default.

### Follow-ups
- If OHLC/volume charting needs expand to another surface, prefer reusing
  `lightweight-charts` there too rather than reaching for a third library.

## Alternatives considered
- **`chartjs-chart-financial` plugin (stay Chart.js-only)** — rejected: thinly
  maintained, and mixing candlestick + volume + baseline on one canvas requires manual
  multi-axis wiring since Line/Mountain/Baseline aren't first-class Chart.js concepts the
  way they are in lightweight-charts.
- **Hand-rolled SVG/Canvas rendering** — rejected: reinventing candlestick rendering,
  crosshair sync, and volume panes from scratch for something a well-tested library
  already solves is unnecessary custom-code risk.

## Agent provenance
- **Decided by:** orchestrating agent (Claude Sonnet 5) during the 2026-07-16
  asset-chart-types brainstorming session, on branch `feat/batch-aware-dedup` context but
  implemented as its own scoped change; owner approved the library choice conversationally
  before going AFK.
- **Driving artifact:** `docs/superpowers/specs/2026-07-16-asset-chart-types-design.md`.
- **Why this way:** the requested chart-type names are literally lightweight-charts'
  series-type vocabulary — strong signal it's the right-shaped tool, minimizing custom
  code versus bolting the same feature set onto a library not built for it.
