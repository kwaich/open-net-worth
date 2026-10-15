---
number: 2026-10-01-money-flow-is-a-server-built-svg-sankey
title: Money flow is a server-built graph drawn as an SVG Sankey (d3-sankey layout)
status: Accepted
date: 2026-10-01
deciders: orchestrating agent (Claude) + repo owner
---

# Money flow is a server-built graph drawn as an SVG Sankey

## Context

The Dashboard's `rep-sankey` widget ("Cash-flow Sankey") shipped as two ranked bar lists: the
2026-07-22 Overview port judged a charting dependency disproportionate for one widget. The owner
now wants the real diagram — income → groups → total → spending groups → categories — on the
Dashboard AND behind Plan › Cash Flow, and wants the two to be different components (a glanceable
card vs. a full, drillable view). Every figure must stay server-computed (ADR
2026-07-31-overview-aggregates-are-server-computed); the only chart library is Chart.js (canvas).

## Decision

We will build the month's flow as a node/link graph in `services/cash_flow_graph.py`
(`GET /api/cashflow/flow?month=&compact=`) — every amount and share, the "Other" folding
(< 2% of the flow, max 8 per group), the Saved node, and a "From savings" source when spending
exceeds income so every column reconciles. The browser only positions it: `d3-sankey` computes
geometry in one pure module (`components/shared/moneyFlowLayout.ts`) and two separate React
components draw SVG — Plan's `MoneyFlowModal`/`MoneyFlowChart` (5 columns, month stepper, table
view, category drill-down to the register) and the Dashboard's `CashFlowCard` (3 columns, no
controls). The rollup's old `flow` field is removed.

## Consequences

### Positive
- The Sankey reconciles by construction (tested per column and per node in/out).
- SVG gives real elements: hover tracing, keyboard focus, click-through, theme tokens, jsdom tests.
- One pure layout shared by two independently designed UIs.

### Negative
- A second charting dependency beside Chart.js (`d3-sankey` + `d3-array`/`d3-shape`, ~15 KB).
- A hand-drawn chart: tooltip, label collision and privacy masking are ours to maintain (SVG text
  can't take `.mask`'s overlay, so `.mf-lbl-amt` hides itself under `hide-amounts`).

### Follow-ups
- A Lucius tool for the flow graph, if asked for.

## Alternatives considered
- `chartjs-chart-sankey` — rejected: weak node click targets, limited two-line labels, canvas is
  untestable under jsdom, colours need `resolveColor` conversion.
- Keep the bar lists — rejected by the owner.
- Client-side folding of the rollup's rows into a graph — rejected: duplicate money math.

## Agent provenance
- **Decided by:** orchestrating Claude agent, with the owner choosing SVG over the Chart.js plugin.
- **Lane / branch:** main (desktop session, 2026-10-01).
- **Alternatives weighed:** Chart.js sankey plugin vs. d3-sankey + SVG vs. the existing lists.
- **Driving artifacts:** owner request + `design/cashflow-sankey/mockup.html`.
- **Why this way (one line):** a reconciled server graph plus SVG marks is the only shape that is
  both correct by construction and interactive.
