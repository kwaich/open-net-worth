# Components for an extra section

Read this only when the period calls for an extra section (`EXTRA` in `references/slots.md`).
Every component below is styled by the app's stylesheet, so an extra section looks like the rest
of the page. The limits in `slots.md` apply here too.

## Rules every component keeps

- Use only the classes shown here — the stylesheet defines them, light and dark, screen and
  PDF. Never add a `<style>` block or a `style=` attribute, except a bar's `width`/`left` below.
- Side by side means a `<table>`, never flex or grid — the PDF engine lays out only blocks and
  tables.
- Escape text: `&` → `&amp;`, `<` → `&lt;`.
- Amounts carry the currency code with two decimals and thousands separators (`<CUR> 1,380.25`),
  percentages one decimal, short dates (`22 Sep`), gains "+", losses "−" (a real minus).
  Separate meta text with commas, never ` · `.
- Emphasis is for the one thing a reader must not miss — when everything is bold, nothing is.

## Components

**Section** — a titled part of the story. `onw-lede` is an optional one-line intro.

```html
<div class="onw-section">
  <h2>Day-to-day spending</h2>
  <p class="onw-lede">Against your average month, the last 6.</p>
  …components…
</div>
```

`<h3>` inside a section starts a sub-part.

**Key figures** — two columns of label, value, one line of context. A `wide` cell spans both.

```html
<table class="onw-figures">
  <tr>
    <td><span class="onw-label">Earned</span><span class="onw-value"><CUR> 17,421.08</span><span class="onw-sub">Salary and a dividend</span></td>
    <td><span class="onw-label">Kept</span><span class="onw-value"><CUR> 1,530.00</span><span class="onw-sub">8.8% of what you earned, goal 35%</span></td>
  </tr>
  <tr><td class="wide" colspan="2"><span class="onw-label">Net worth</span><span class="onw-value"><CUR> 425,310.12</span><span class="onw-sub">Up <CUR> 3,120.10 since 1 Sep</span></td></tr>
</table>
<p class="onw-total">One quiet sentence under the figures.</p>
```

**Rows** — a name with a line of context on the left, an amount on the right. Use them for any
list: categories, payments, trades, bills, envelopes, holdings.

```html
<table class="onw-rows">
  <tr><td>Bought RKLB<span class="onw-sub">85 units over 3 trades, 3–4 Sep, IBKR</span></td><td class="r"><CUR> 6,860.51</td></tr>
  <tr><td>Sold AMD<span class="onw-sub">16 units at USD 600.00, 21 Sep</span></td><td class="r onw-gain">+<CUR> 10,005.28</td></tr>
</table>
```

`onw-gain` / `onw-loss` colour a value (the sign carries it too). `<span class="onw-flag">…</span>`
under a name marks something that needs them.

**Bars** — rows with a bar under each, on ONE scale (the largest value shown is 100). The mark
is a comparison (their usual, a budget) and appears on screen only; add the key when you use it.
`onw-high` colours a bar that ran well above its comparison.

```html
<table class="onw-rows">
  <tr class="onw-has-bar"><td>Dining out<span class="onw-sub">average <CUR> 151.04</span></td><td class="r"><CUR> 2,653.39</td></tr>
  <tr class="onw-bar-row"><td colspan="2"><div class="onw-track"><div class="onw-fill onw-high" style="width:100%"></div><span class="onw-mark" style="left:5.7%" data-screen-only></span></div></td></tr>
</table>
<p class="onw-key"><i></i>your average month</p>
```

**Text** — a plain `<p>` inside a section, for the rare finding no component can carry (a
recommendation). Keep it to two or three sentences; a run of paragraphs is a sign the section
wants rows or a chart.

**Callouts** — `<p class="onw-clear">No unusual charges this month.</p>` for a check that came
back clean.


## Charts

Charts are drawn by the server from data — never draw SVG yourself. Put a block where the chart
goes; at most 12 per page.

```html
<script type="application/x-onw-chart">{"type": "line", …}</script>
```

- `line` — values over time: `{"type": "line", "format": "percent", "points": [{"date": "2026-09-01", "portfolio": 0.0, "benchmark": 0.0}, …], "series": {"portfolio": "Your portfolio", "benchmark": "S&P 500"}}`.
  Up to 3 series; `percent` reads values as fractions (0.024 = 2.4%), so `get_returns`' `series`
  pastes in verbatim. Use `"format": "money", "currency": "<CUR>"` for amounts.
- `stack` — parts of a whole in one bar, each against an optional target:
  `{"type": "stack", "format": "percent", "items": [{"label": "Stocks", "value": 72.5, "target": 60}]}`.
- `diverging` — gains right, losses left: `{"type": "diverging", "format": "money", "currency":
  "<CUR>", "items": [{"label": "AMD", "value": 10005.28, "note": "+48.2%"}]}`.

Reach for a visual whenever the point is a comparison: bars for categories against their usual
or budget, `line` for a trend, `stack` for a mix against its target, `diverging` for winners
against losers. A chart of one number is a figure, not a chart.
