# Price alerts redesign — parity and gaps (owner review)

Companion to `docs/adr/2026-09-21-price-alerts-redesign-faithful-port.md`. The Claude Design
page "Price Alerts Redesign" (project "Open Net Worth UI") is the spec; this file lists
everything the shipped modal does NOT do exactly as the prototype, and everything the app did
before that the design has no home for. Nothing here was dropped silently.

## Prototype features shipped with a real-data seam (behaviour identical, data real)

| Prototype | Shipped |
|---|---|
| `TICKERS` mock (price, chg, lo, hi) | live price from the caller's quote; day change from the asset-detail quote or `prev_close`; 52-week range from `GET /api/intel/assets/{id}/stats` |
| `ALERTS0` mock rows | `GET /api/alerts?ticker=` — the alert store (ADR 0014) |
| per-row `enabled` toggle (local state) | `PATCH /api/alerts/{id} {enabled}` — the same field the monitor's disable-after-fire writes |
| `onMute` / `onFeeds` (local state) | `PATCH /api/watchlist/items/{id}` with `monitor_news` / `custom_feed_urls`, committed per control |
| `suggestAll()` (two fixed multipliers) | the app's `luciusSuggestAlerts` engine (position- and target-aware), each suggestion now named |
| `name`, `mode` (% move), `channel` | new store/API fields `name`, `kind`, `notify` — see the ADR |
| `fired: '04 Jul'` (never rendered) | `last_fired_at` (stored; likewise not rendered — the prototype does not show it either) |

## Deliberate deviations from the prototype

- **Ticker select is locked on New alert too.** The prototype's New-alert form offers a
  ticker dropdown because its demo shell has no ticker context; every caller in the app opens
  the modal FOR a ticker, so the select shows that one ticker, disabled (the prototype locks it
  only when editing).
- **"Remove from watchlist" stays in the manage footer** (left of the primary action, `.pa-del`
  styling) when the caller allows it (`canRemove`). The prototype has no such action, but the
  Intel watchlist rail has no other removal path for a ticker; dropping it would be a
  functional regression the owner could not discover from this file alone. Say the word and
  it goes.
- **A save that fails is shown, not swallowed.** The prototype has no failure state. A failed
  write leaves the form/list as it was and prints "Could not save — please try again." in the
  footer (`.pa-err`, the one class not in the prototype's stylesheet); a feed URL the server
  rejects shows the server's own reason.
- **Mute / News sources are hidden for a ticker on no watchlist.** They live on the watchlist
  item (ADR 0014, PAD-6), so a standalone alert has nowhere to store them. The prototype shows
  them unconditionally because its demo ticker is always followed.
- **A `% move` row's distance column shows today's move** (`+1.4% today`) instead of "% away",
  and it has no tick on the 52-week track (it is not a level). The prototype only rendered
  price rows in the list, so this is an extension, not a change.
- **Back label is caller-supplied**: "Back to watchlist" from Intel, "Close" from the
  asset-detail rail. The prototype hard-codes the former.
- **The "Default query" text is the prototype's wording verbatim.** It describes the intent of
  what Lucius does when the alert fires. The literal prompt the monitor sends is built server-
  side (`price_monitor._build_alert_prompt`) and carries position, profile and news context;
  the two are not the same string. Follow-up if the owner wants the shown text to be the
  literal prompt.

- **The three form selects carry the design-system chevron.** The prototype (and the DS
  bundle it was built from) shows the browser's native select arrow; the owner asked for the
  house chevron instead (review 2026-09-21). Implemented as a `.pa-sel` wrapper with the
  same lucide ChevronDown the feeds header uses, the same local pattern as `.pl-selwrap` and
  `.firstrun-select-wrap` — three copies now, a candidate for promotion into `ui/Select`.
- **The Alert-criteria segmented control is forced to fill its column.** The prototype's
  bundled SegmentedControl was `display:flex` and stretched; the app's primitive has since
  become inline-flex + `align-self:flex-start`, which packed the segments and wrapped
  "Target price" onto two lines. `.pa-row2 .ui-segmented` restores the prototype geometry.
- **No rule under the price header.** The prototype paints `border-bottom` under the header;
  with the track now drawing its own line it was a second separator doing the same job, and
  with no track (no price, or no price levels) it sat as an orphan hairline over the next
  card. Removed in every state (owner review 2026-09-21); the header keeps its 16px of
  bottom padding.
- **The 52-week track is painted.** The prototype's `.pa-scale` bar is `--surface2`, which the
  token file makes identical to the modal background, so the line never showed — in the
  prototype's own render either. The owner confirmed the line is meant to be seen (review
  2026-09-21), so the bar uses `--line-strong` in every theme.
- **Tick labels stagger when levels cluster.** The prototype's demo levels are spread out; a
  real book has stops a few dollars apart, and on a phone their labels overprinted (owner
  report 2026-09-21). `layoutTickRows` measures the track and drops a colliding label to a
  second or third row, growing the track's bottom margin so the 52w range line keeps clear.
- **Light and warm themes get a darker tick.** The prototype is dark-only; its `--line-strong`
  tick nearly vanishes on the light surfaces, so those themes use `--fg-3` for the tick. The
  dark theme is untouched and matches the prototype's own render pixel for pixel.

## Prototype behaviour NOT ported

- `.ui-modal__body { padding-right: 12px; margin-right: -12px }` — the prototype's own
  scrollbar gutter. The app's `Modal` already pins head/footer and reserves the gutter with
  `scrollbar-gutter: stable`, so the override is redundant and would double-shift content.
- The `.sw` state switcher — a review affordance, not part of the design.

## Present in the app before, no home in the design

- **Per-row "Draft / Rewrite" instruction drafting** (`LuciusDraftLink`) — the old modal could
  draft an instruction for a row from the engine. The design's form has a plain textarea with a
  placeholder; the suggestion set covers the "what should I set" job instead. Follow-up if the
  owner wants a Draft link inside the form.
- **The AI tools do not yet set `notify`.** `set_price_alerts` / `update_price_alerts` accept
  `name` and `kind` (a day-move alert, 2026-09-29); a per-alert channel is REST/UI-only until
  the tools grow that field.
