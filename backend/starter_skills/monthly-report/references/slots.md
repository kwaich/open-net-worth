# Filling `assets/report.html`

Read this when you fill the template, after you have gathered the data and decided what the month
was about.

## Mechanics

- Copy the file verbatim and change only the slots: never edit class names, element order or
  wording outside a slot, and never add a `<style>` block or a `style=` attribute. Keep
  `<!-- onw:design -->` and `<!-- onw:logo -->` exactly where they are — the server swaps in
  the design system and the logo when the page is saved.
- A slot is an UPPER_CASE name in double curly braces. Replace it, braces included, with its
  value, HTML-escaped (`&` → `&amp;`, `<` → `&lt;`). Chart slots (`*_CHART`) take raw JSON, not
  escaped.
- Prose slots are rich text — format them however helps the reader. In `SUMMARY`, the `*_LINE`
  and `*_LEDE` slots, `LOOK_DETAIL` and `NOTE`: `<strong>` (the number or phrase they must not
  miss), `<em>` (a name, an aside), `<mark>` (a highlight) and `<br>`. `SUMMARY` may also hold
  `<p>` paragraphs and a `<ul>`/`<ol>` list when separate findings read better as a list. The
  design system styles all of these; use no other tags and no `style`/`class` attributes, so
  every report still looks like the app. Formatting is emphasis — when everything is bold,
  nothing is. Figures and amounts in their own slots are already styled; leave them plain.
- `<!-- repeat: name -->…<!-- /repeat -->` is one row: write it once per row and drop both
  markers. `<!-- optional: name -->…<!-- /optional -->`: keep the block without its markers when
  it has content, delete it with its markers when it would be empty.
- `create_artifact` refuses a page with a slot or marker left over and names it — fix the page
  and call it again.
- Amounts keep the tools' currency code (`<CUR>` here) with two decimals and thousands
  separators: `<CUR> 1,380.25`. Percentages to one decimal. Short dates: `22 Mar`. Gains with
  "+", losses with "−" (a real minus). Separate meta text with commas, never ` · `.
- Bar positions (`*_BAR_PCT`, `*_COMPARE_PCT`) are drawing positions, 0–100, one decimal — yours to
  compute. Every printed number comes from a tool, or from your own working on a tool's rows.

## How much to say

The page is read on a phone, so it is short by design. `create_artifact` refuses a page over these
limits and names what to cut:

- `SUMMARY` — at most three sentences, about 50 words (refused over 80).
- Every context line (`*_LINE`, `*_COMPARE`, `*_DETAIL`, a row's grey line) — about 8 words and
  one number (refused over 12 words or 3 numbers). It says what the figure means; it is not a
  second sentence of the summary.
- At most 6 rows in any list (refused over 8): the largest first, the rest named in the lede.
- No free paragraphs, except one recommendation in an extra section (refused over 2, or over 60
  words each).
- Never narrate method — what the tools exclude, how instructions were followed. A caveat that
  changes how a number reads is one data note; at most 4 notes (refused over 4, or a note over
  30 words).

## What a payment was for

Bill, subscription and payment items carry the row's full `description` (bank text) and `memo`
(the user's note). When the payee name alone doesn't say what was bought, the row's context
line does, in plain words from them: "Apple" → "beIN Sports Connect, monthly"; "GIGA" → "300 GB 5G plan".
Never paste raw bank text, receipt or order numbers, card or account digits, or times.

## Page and summary

- `NAME` — the profile's full `name` ("Prepared for <NAME>"). Everywhere else use the
  `nickname`, or the first name.
- `HEADING` — "Monthly report". `PERIOD` — "September 2026", and nothing else (no timezone,
  currency or as-at note). `TITLE` (browser tab) — "Monthly
  report, September 2026".
- `SUMMARY` — two or three sentences, at most about 45 words, carrying your findings, most
  important first, each with its number and its reason; not the totals the figures already show. "You kept 9.1% of what you
  earned against a 35% goal. Day-to-day spending was <CUR> 4,180, about 22% above your average
  month, mostly travel and dining out. The portfolio rose 2.4%, half a point ahead of the S&P
  500."
- Ledes (`*_LEDE`) — at most 12 words, or an empty string.
- `notes` block — only a caveat that changes how a figure ON THE PAGE reads (an estimate, a
  figure that can't be shown), at most 4, one short sentence each. Never the currency, what the
  tools exclude, a check that found nothing, or how the page was built. One `NOTE` each;
  `NOTES_COUNT` is how many. Delete the
  block when there are none.
- `FOOTER` — "Prepared for <nickname> by Lucius on <date>. Read-only report, no records were
  changed."

## Figures

- `INCOME` (`income.earned.total`); `INCOME_LINE` names what was earned, and beside it any
  `income.paid_back.total`: "Salary and a dividend. Plus <CUR> 250.00 paid back to you."
- `KEPT` (`get_transactions`' `net_base`); `SAVINGS_RATE_LINE` — "<rate>% of what you earned,
  goal <target>%", or just the rate without a target; omit the rate when nothing was earned.
- `DAY_TO_DAY` (`spending.day_to_day.total`); `DAY_TO_DAY_LINE` against the average month and
  last year: "22.4% above your average, 15.1% above last September".
- `BILLS` — `spending.bills.total` less the subscriptions you list below; `BILLS_LINE` — "Rent,
  tax and phone. Plus <CUR> 182.40 in subscriptions", or "1 expected bill did not arrive".
- `NET_WORTH` — `get_net_worth(as_of=<the month's last day>)`; `NET_WORTH_CHANGE` — against
  `get_net_worth(as_of=<the previous month's last day>)`: "Up <CUR> 3,120.10 since 31 Aug". Never
  today's figure; "Not shown" / "See data notes" only when a date figure is implausible.
- `TOTAL_LINE` — exactly this one sentence, nothing appended (caveats go in the notes): "In total <CUR> 7,180.40 went out, against <CUR> 6,920.10 in August."
  (`spending.total`, `prior.total`).

## Sections

- `budget` block: `BUDGET_LEDE` ("Where your income went, against your targets."), then
  `BUDGET_CHART`. Inside it, the `over` block: `OVER_LEDE`, at most 3 `over` rows, the largest
  overruns by money first — `ENVELOPE`, `ENVELOPE_SPENT`, `ENVELOPE_BUDGET`, `ENVELOPE_OVER`
  (the tool's negative available, shown positive); `OVER_MORE` names the rest ("Also over:
  Travel, Shopping.") or is empty.
- `category` rows — day-to-day categories, largest first, at most 6: `CATEGORY`,
  `CATEGORY_AMOUNT`, `CATEGORY_COMPARE` ("average <CUR> 420.10", or "new this month"),
  `CATEGORY_CLASS` (`high` when `vs_usual_pct` ≥ 25, or new and over 10% of day-to-day;
  otherwise empty). One scale for all bars: the largest `amount` or `usual` of any row;
  `CATEGORY_BAR_PCT` = amount ÷ scale × 100, `CATEGORY_COMPARE_PCT` = usual ÷ scale × 100.
  `SPENDING_LEDE` from `usual.basis` — "Against your average month, the last 6."
- `item` rows — `spending.day_to_day.largest`, at most 3: `PAYEE`, `ITEM_DATE`,
  `ITEM_CATEGORY`, `ITEM_AMOUNT`.
- `bills` section — ALWAYS present and COMPLETE in the monthly report (people check it most):
  every bill and every subscription of the month, nothing dropped (both lists are exempt from
  the row limit). `BILLS_LEDE` (usually
  empty), a `bill` row each, largest first — `BILL`, `BILL_DETAIL` ("1 Sep,
  monthly"), `BILL_AMOUNT`; the `bills-missing` block's `BILLS_MISSING` names each one that did
  not arrive.
  Several bills under one of THEIR categories may be one row named by it, payees in the detail
  ("Startup Costs" — "AWS, Anthropic, OpenAI, Google Workspace"). Fees are never bill rows —
  they are a row in Worth a look, and `BILLS` is reduced by them.
- `subscriptions` block (inside `bills`; delete when there are none): recurring charges for a
  service they chose and can cancel (streaming, music, cloud storage, an AI or software plan).
  THEIR structure decides first: a row whose category sits in their Bills envelope group is a
  bill, and one in their "Subscriptions" category is a subscription, whatever it looks like (a
  Google Workspace plan they file under a Bills-group category is a bill). Your judgement only
  for rows their labels leave open; when unsure, it is a bill. `SUBSCRIPTIONS_TITLE` is
  their own word for it (that category's or group's name), else "Subscriptions".
  `SUBSCRIPTIONS_LEDE` — the count and total in one line ("6 services, <CUR> 182.40"), plus one
  that rose or looks unused when there is one; a `subscription` row each, largest first —
  `SUBSCRIPTION`, `SUBSCRIPTION_DETAIL` ("30 Sep, monthly"), `SUBSCRIPTION_AMOUNT`. Every other
  recurring row is a `bill` row: rent, tax, loan repayments, phone, utilities, insurance.
- `investments` block (whenever there are holdings): `INVEST_LEDE` ("1 to 30 September,
  time-weighted."), `PORTFOLIO_VALUE` (`end_value`), `PORTFOLIO_START_LINE` ("<CUR> 402,118.40
  at the start"), `PORTFOLIO_RETURN` ("+2.4%") with `RETURN_CLASS` `gain`/`loss`,
  `BENCHMARK_LINE` ("S&P 500 +1.9%, so 0.5 points ahead", or "No benchmark comparison
  available"), `RETURN_CHART`, `PORTFOLIO_GAIN` (`period_gain_base`) with `GAIN_CLASS`; the
  `realized` block (`REALIZED`, `REALIZED_CLASS`, `REALIZED_LINE` naming the instruments, "Sold
  16 AMD") and `invest-income` block (`INVEST_INCOME`) only when they apply. Never add these
  together — the tools don't say whether the period gain contains the realized part.
- `allocation` block: `ALLOCATION_LEDE` ("Your mix against your target."), `ALLOCATION_CHART`;
  delete it when no target is set.
- `holdings` block: `HOLDINGS_LEDE` (often empty; say so when `holdings_omitted` is present),
  `HOLDINGS_CHART`.
- `trades` block (whenever anything was bought or sold): purchases from `get_lots(date_from,
  date_to)` (`paid_base`), sales from `get_realized_pnl` over the same dates. `TRADES_LEDE` —
  the counts and totals in one line: "10 purchases, <CUR> 24,896.67; 1 sale, <CUR> 12,252.86."
  A `trade` row per instrument (several purchases of one instrument are one row), sales first,
  then the largest purchases, at most 8 — `TRADE` ("Bought RKLB"), `TRADE_DETAIL` ("85 units,
  3–4 Sep"), `TRADE_AMOUNT`. Every trade the lede counts is in a row or named: when one doesn't
  fit, the lede ends "Also bought QQQ." Shares moved between brokers or vested are not
  trades.

## Chart data

- `BUDGET_CHART` and `ALLOCATION_CHART`: `{"type": "stack", "format": "percent", "items":
  [{"label": "...", "value": <share>, "target": <target>}]}` — for the budget, the method's
  groups in its own names and order, each group's share of income and its target exactly as
  `get_budget_view` states them; for allocation, one item per asset class from
  `get_rebalance_plan` (`current_pct`, `target_pct`).
- `RETURN_CHART`: `{"type": "line", "format": "percent", "points": <get_returns' series,
  verbatim>, "series": {"portfolio": "Your portfolio", "benchmark": "<benchmark name>"}}`. Drop
  "benchmark" without a comparison; delete the `return-chart` block when the series is empty or
  `data_quality.sufficient` is false.
- `HOLDINGS_CHART`: `{"type": "diverging", "format": "money", "currency": "<CUR>", "items":
  [{"label": "<short name>", "value": <gain_base>, "note": "<return_pct, signed, one
  decimal>%"}]}` — up to 3 biggest gains, then up to 3 biggest losses by `gain_base`; a null
  `return_pct` gets the note "new".

## Worth a look

- Always present: only what they can ACT on, largest money at stake first, at most 6 rows.
  `LOOK_LEDE` — one line saying what you CHECKED, with the verb, so a count never reads as a
  count of problems: "Checked all 62 transactions this month across 9 accounts, plus fees and
  subscriptions." Never an account list, never in the data notes. Keep exactly one of
  `all-clear` (`ALL_CLEAR`: "Nothing needs you this month.") or `looks`: a `look` row each —
  `LOOK_KIND` ("Unusual charge", "Fees", "Subscription up", "Bill missing", "Uncategorised", "Over budget"), `LOOK` (the merchant or thing), `LOOK_DETAIL` (why, and the action, in
  one short line: "29 Sep, Citi card. New foreign merchant — confirm it."), `LOOK_AMOUNT`.
- What qualifies: an unusual charge (the checks in SKILL.md); fees — any fee paid in the month
  (`LOOK_AMOUNT` is the month's fees, `LOOK_DETAIL` the last 12 months' total, its main source and
  a practical action); a subscription that rose or looks unused; an expected bill missing or
  changed (`bills_due_not_seen`); uncategorised transactions worth sorting
  (`get_uncategorized_summary`); an envelope over budget by real money ("move <CUR> 120 from Travel").
- Fee totals come from TOOLS, never your own sum of rows: choose EVERY fee category by name —
  there are usually several (bank, brokerage, a card's annual fee, conversion), and the annual
  fee is often the largest — and pass them all as one `categories` list (and check the month's rows for fee payees in other categories), then take
  `get_transactions(category=…, date range, summary_only=true)`'s total plus
  `get_fees_summary`'s `total_fees_base` for the same dates.

## Extra sections

`EXTRA` is empty, or one or two sections the period genuinely calls for that the page above does
not already show — a run of trades worth its own look, a refund or one-off that changed the
picture, a goal that came closer. Build them from `references/components.md` (section, rows,
bars, a chart, at most one short paragraph for a recommendation), under the same limits. Never
repeat a figure the page already shows. A recommendation is about what to do NEXT, so its
section is titled for the coming period ("For October"), never the reported one's ("September's
priority").
