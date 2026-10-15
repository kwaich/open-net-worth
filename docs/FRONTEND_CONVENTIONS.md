# Frontend Conventions (read before building any UI)

The agents that build UI **cannot see the rendered screen** — so the layout
rules that aren't obvious from a single component must be written down and
checked. This is that checklist. **Read it before adding/changing any tab,
modal, drawer, or overlay, and run the "Definition of visually-done" checklist
at the end.**

## The app shell

```
<div class="app-shell [chat-open]">     // flex row; gets `chat-open` when the chat panel is open
  <nav class="sidebar [expanded]">       // 52px collapsed / 200px expanded, icon-only → icon+label
  <div class="app-layout">               // main content (the active tab) + .topbar
</div>
```
- The active tab renders inside `.app-layout`. New tabs slot into `tabs.ts`'s
  `TABS` / `TAB_LABELS` / `TAB_SUBTITLES` records and `RouterShell.tsx`'s
  `TAB_ICONS` record **and** its render switch — miss one and strict-TS fails
  (`Record<TabId,…>`). `App.tsx` itself is now a thin auth-gate wrapper around
  `<RouterProvider>` (`src/router.tsx`) — it owns none of this.

## ⚠️ The chat panel — the #1 thing new UI forgets

Lucius's chat surface has **two fixed-position states**, both owned by
`RouterShell.tsx` (which renders whichever one applies):
- **Open** — the side panel (`ChatPanel.tsx`) is **`position: fixed` on the
  right**, **user-resizable**, and toggles open/closed (persisted in
  `localStorage` `chat-open`). `RouterShell.tsx` adds the **`chat-open`**
  class to `.app-shell` when it's open, which applies `margin-right:
  var(--chat-width)` to the main content so the tab reflows; `ChatPanel`
  writes **`--chat-width`** onto `<html>` (`document.documentElement`).
- **Closed** — a floating collapsed pill/expanded-composer dock
  (`components/chat/lucius/LuciusDock.tsx`, `.lx-dock`) renders instead,
  **also `position: fixed`** (bottom-right, high z-index). It has no
  `--chat-width` footprint (nothing reflows for it), but it still PAINTS OVER
  page content in its corner.

**Rule: every `position: fixed` / overlay element (modals, drawers, toasts,
popovers) MUST account for BOTH chat states** — clear `--chat-width` when the
panel is open (otherwise it renders *underneath* the fixed panel — this is
exactly the bug where the "New skill" modal hid behind the chat), and avoid
the dock's bottom-right corner when it's closed. `make ui-gate`'s visual-qa
pass asserts nothing else paints on top of the expanded `.lx-dock-inner` card,
so a new fixed element that ignores it fails the gate, not just a code review.

- Modals use the shared `.modal-overlay` (`position: fixed; inset: 0`). It must
  stop at the chat panel: `right: var(--chat-width, 0px)` so the dimmed area +
  centered card live in the **visible** region, not under the chat.
- For this to work, `--chat-width` must be **`0` when the chat is closed** (set
  it from the panel's `open` state, not just its width).
- **Always test a new screen with the chat panel BOTH open and closed.**

## Approval (confirm) cards

A card Lucius shows before a write follows its own design system. It uses two text sizes
(12px body, 10px column caps) and a header → lead → meta → facts → details order. It has no
paragraphs and states each fact once. Every card type is built from
`components/chat/approval/ApprovalCard.tsx`, and two tests fail on drift. To review them all,
open `/?gallery=cards` on the dev server. Rules and anatomy are in
[APPROVAL_CARDS.md](APPROVAL_CARDS.md).

## ⚠️ Building a NEW tab (this is where things go off)

Edits to an existing tab inherit the house style by osmosis. A **brand-new tab
has no neighbor to copy**, so it drifts — its own intro block, its own card
styles, its own spacing. Skills and Rules drifted for exactly this reason. So:

1. **Clone an exemplar.** Open a feature-rich existing tab — **`Investments.tsx`
   or `Cash.tsx`** — and mirror its structure (section rhythm, `.panel`
   wrappers, summary row, density). Do not invent a new page skeleton.
2. **Don't add a second heading.** The tab title + subtitle already render from
   `tabs.ts`'s `TAB_LABELS` / `TAB_SUBTITLES` (via `RouterShell.tsx`). Don't
   also hand-roll an intro `<h1>` + paragraph in the tab body (Skills did — it
   reads as duplicated).
3. **Reuse the shared building blocks**, don't re-style your own:
   `components/shared/` — `SummaryBar` (stat-card row), `HoldingsTable`,
   `AllocationTargetBars`, `RiskPanel`, etc. — and `.panel` for sections.
4. **Match the exemplar's spacing/type scale** rather than picking new sizes.
5. Then run the visually-done checklist below (chat open/closed especially).

## Modals

Use `<Modal>` from `components/ui` for every modal form or manager. It wraps
`@radix-ui/react-dialog`, which owns the portal, inert background, focus containment and
restoration, Escape, nested-dialog ordering, and body-scroll lock. Never hand-roll
`.modal-overlay`, `role="dialog"`, focus traps, or document scroll locking in a screen.

The shared classes own geometry: centered `sm/md/lg/xl` cards on desktop, and one bottom-docked
`92dvh` sheet below 640px with internal body scrolling, sticky chrome, safe-area clearance, and
reduced-motion behavior. `workspace` is the deliberate full-viewport manager exception.

All create/edit forms use `modal-card` + `btn-primary` (green) / `btn-secondary` — never a bespoke card or button (fold any form-specific sizing into a `.modal-card--<name>` modifier). Field labels use the shared `.form-label` class (Title Case) — never per-component `text-transform` (FE-2).

## ⚠️ Compose from the UI kit (`src/components/ui/`) — the #1 rule

The app now has a **design-system primitive library** in `src/components/ui/`
(built from the audit in `.claude/rfcs/phase3/design-system-audit.md`). **Never
hand-roll a button, input, select, checkbox, modal, panel, badge, chip,
segmented control, or empty/loading/error state** — compose the primitive:

`<Button variant size>` · `<Modal>` · `<FormField>` + `<TextField>`/`<NumberField>`/`<TextArea>`/`<Select>`/`<Checkbox>`/`<CheckboxGroup>` · `<Panel>` · `<SummaryBar>` · `<Tag>`/`<TagInput>` · `<Badge>` · `<SegmentedControl>` · `<EmptyState>`/`<Loading>`/`<ErrorState>`.

- A raw `<button>` / `<input>` / `<select>` or a fresh `.modal-overlay` in a tab
  is a **review reject**. New variant needed? Extend the primitive, not a
  per-screen override.
- **Never hardcode a dimension or color.** Use the scale tokens
  `--space-*` (4/6/8/10/12/16/20/24), `--text-*` (10–22), `--radius-*`
  (xs→pill), `--shadow-modal`, and the color tokens below. No raw px/hex in new
  CSS or inline styles.
- `<Modal>` already handles the overlay + **chat-clearance** (`--chat-width`) +
  click-out + Escape — so the chat-panel rule above is satisfied for free.
- Add/changed a primitive? Add its entry to the gallery (`/?gallery=1`) in the
  same PR so it stays screenshot-reviewable.
- **Theming / white-label = override `:root` tokens only** (this is the ON
  white-label foundation).

## Theme tokens (use these, never hard-coded colors)

`--bg` (page) · `--surface` / `--surface2` (cards/inputs) · `--line` (borders)
· `--ink` (primary text) · `--muted` / `--faint` (secondary/tertiary) ·
`--ibkr` (accent/focus) · `--warn` · `--bad`. Plus the scale tokens above.
Prefer the **`ui/` primitives** over the bare `.btn-*` / `.form-label` classes
(those now live behind `<Button>` / `<FormField>`). Match an existing tab's density.

## Layout, breakpoints & typography (the layout primitives)

Design: `.claude/rfcs/phase3/design-system-layout-rwd.md`. **Compose screens from
the layout primitives — don't hand-roll page width, grids, or font sizes:**
- `<Container size>` — caps + centers the content well (`default` 1200, `wide`
  1440 for table-heavy tabs, `narrow` 760, `full`). Wide screens stop running
  ragged / leaving empty voids.
- `<Grid min gap>` — responsive `repeat(auto-fit, minmax(min(<min>,100%),1fr))`
  that **stretches** children to fill the row (NOT `auto-fill`, which leaves the
  empty void — that was the Skills bug).
- `<Stack gap>` (vertical rhythm) · `<Cluster gap wrap>` (wrapping toolbars/chip
  rows) · `<PageHeader title subtitle actions>`.

**Canonical page shell (every tab looks like one app).** A tab renders its OWN
`<PageHeader>` INSIDE its `<Container>` — so the title/subtitle/right-actions and
the content below share one gutter + max-width (and the actions wrap under the
title below 640). Settle on `<Container size="wide">` (matches Intel / Ledger /
Connectors / Investments) unless the tab is genuinely form-centric (`narrow`).
The shared `.tab-header` is suppressed for any tab that self-renders its header
(`SELF_HEADER_TABS` in `tabs.ts`) — do NOT render both, and do NOT leave a tab's
title in the shared `.tab-header` (full-bleed) while its content sits in a centered
`Container` (that's the gutter-mismatch this convention fixes). The one exception
is the Accounts tab: its full-height two-panel workspace (`app-layout--panel`)
opts out of the Container well, and its App `.tab-header` is already aligned to
the panel gutter.
- `<Text size tone weight truncate>` — **all text uses this** (one place maps
  `size`→`--text-*`); **no raw `font-size` in new code.** `truncate` REQUIRES a
  `title={fullValue}` so labels never hard-clip silently.
- **Breakpoints (single source):** mobile `<640` · tablet `640–1024` · desktop
  `>1024`; JS reads `BP` from `src/components/ui/breakpoints.ts`; new media
  queries use exactly these boundaries.
- **Modal sizes:** `sm`/`md`(520)/`lg`(720, default for 2-col forms)/`xl`(960,
  wide editors); every modal auto-becomes a bottom sheet below 640.
- Every flex/grid child with shrinkable text needs `min-width:0` — the primitives
  bake this in (it's the usual cause of clipped text, e.g. the chat panel).

## Card grids (so they don't look ragged)

- Prefer `<Grid>` (above). Raw grid: `repeat(auto-fit, minmax(280px, 1fr))`
  (`auto-fit`, not `auto-fill`).
- Cards in a row should be **equal height** — let the grid stretch (don't set
  `align-items: start`) and pin the action row to the bottom with
  `margin-top: auto` so buttons line up across the row.

## List rows (e.g. a checkbox + label + description)

Two lines, aligned: name on its own line, description below (muted, smaller),
checkbox `align-items: flex-start` + a small `margin-top` so it lines up with
the **name**, not the middle of a wrapped block.

## States — never ship a screen without them

Loading · empty (with a primary CTA) · error (with Retry) · the unhappy data
case (no data yet, foreign currency, a failed fetch). The repo already does this
well (see `Skills.tsx`); match it.

## Definition of visually-done (the checklist)

1. Renders correctly with the chat panel **open** AND **closed** (no element
   hidden behind the chat; modal centered in the visible area).
2. No horizontal overflow / clipped content at common widths.
3. Sidebar **collapsed** and **expanded** both look right.
4. Empty / loading / error states present.
5. Spacing, font sizes, and card density match a neighboring tab.
6. `npm run build` passes and adds **no new** strict-TS errors.
7. (When available) `npm run visual-qa` passes — the headless-browser check that
   screenshots key screens with the chat open/closed and asserts no overlaps /
   overflow.

## Visual QA — `npm run shoot` (minimal Playwright harness)

A minimal headless-screenshot harness now exists for design review / eyes-in-the-loop
on real screens (not just the gallery): `frontend/scripts/shoot.mjs`, run via

```
npm run shoot -- --url http://localhost:8080/ --out /tmp/s.png \
  [--tab transactions] [--selector .ui-modal-card] [--click-edit] [--full]
```

It loads the SPA (auth is dormant on loopback), optionally pins the active tab via
localStorage, optionally opens the first "Edit" modal, and screenshots a selector or
the page — with a hard 60s timeout (hang-safe). Use it to screenshot a screen before/
after a UI change and actually LOOK at it. Requires the dev `playwright` package +
`npx playwright install chromium`. It's a dev tool — the app never imports it.

## The UI enforcement net (lint guards + visual-QA)

The automated guard that stops new code (incl. agent-built phases) from
re-introducing the UI debt the design system was built against. Three legs.

### Leg 1 — stylelint: ban non-token `font-size` (`npm run lint:css`)

`.stylelintrc.json` extends `stylelint-config-standard` (stylistic / prettier-owned
rules disabled so it doesn't fight prettier, plus the legacy-CSS-only error rules
`no-duplicate-selectors`/`block-no-empty` turned off so the existing `index.css`
doesn't error). The one load-bearing rule is `declaration-property-value-allowed-list`
on `font-size`: a value must be a `var(--text-*)` token (or `inherit`/`initial`/…),
never a raw `13px`/`1.2rem`.

```
npm run lint:css        # lints src/**/*.css
```

**Severity is WARNING for now** — the existing `index.css` carries ~380 raw
`font-size` declarations (the known debt, migrated in FE1). Warning surfaces the
count so it can't grow silently, but `lint:css` **exits 0** (it does not block CI
or `make`). **After FE1** (raw-`font-size` count is 0), flip the rule to
`"severity": "error"` in `.stylelintrc.json` and wire `lint:css` into `make lint`
/ the CI lint job so the migrated codebase can't regress.

### Leg 2 — eslint: ban raw inline `fontSize` in TSX

`eslint.config.js` adds a `no-restricted-syntax` rule flagging a raw-value
`style={{ fontSize: 13 }}` (numeric / `'13px'` / negative). A token reference
(`fontSize: 'var(--text-base)'` or a template literal) is NOT flagged, so
design-system code that references the scale is clean.

**Severity is WARNING for now** — ~162 pre-existing raw inline `fontSize` usages
live in not-yet-migrated tabs. `src/components/ui/` is already clean (zero raw
inline `fontSize`), so new design-system code is held to the rule today. `npm run
lint` was already red on the pre-existing strict-rule errors (unrelated, deferred
to FE1); this rule adds only warnings — **no new errors**. **After FE1** (count is
0), flip the array's `'warn'` to `'error'` so a raw inline `fontSize` fails lint.

### Leg 3 — visual-QA assert suite (`npm run visual-qa` / `make ui-gate`)

`frontend/scripts/visual-qa.mjs` (Playwright; an upgrade of `scripts/shoot.mjs`).
Run against an already-running server with `npm run visual-qa`, or self-booting
via `--boot` (spins up its own throwaway disposable instance — isolated
`/tmp` dirs + `ONW_QA_MODE`, never `:8080`/live data — and tears it down on
exit). **`make ui-gate` runs the CSS/token/entity-row lint scripts then
`visual-qa.mjs --boot`, and IS blocking** — it's merge-checklist item 4 in
`UI_QUALITY_GATE.md`.

```
npm run visual-qa [--base-url http://localhost:8080] [--out-dir /tmp/onw-visual-qa]
npm run visual-qa -- --boot        # self-boot a disposable instance (what make ui-gate runs)
```

Against the running server it visits **every tab** (the `TABS` keys from
`src/tabs.ts`, seeded via the `wealthTab` localStorage key) at **3 widths**
(`390` / `834` / `1440`) with the Lucius dock in its default **collapsed pill**
state, plus one extra pass per tab at `1440` that clicks the pill
(`.lx-dock-pill`) to expand it — the way a real user opens the dock, not the
old always-open `.chat-panel` side panel. Per cell it asserts:

1. **No horizontal overflow** — `documentElement.scrollWidth <= innerWidth + 1`.
2. **No console errors**.
3. **Nothing else paints on top of `.lx-dock-inner`** (dock-expanded pass,
   sampled via `elementFromPoint` since the dock is designed to sit over page
   content) — the exact regression class the layout rules call out.

A screenshot per (tab, width[, chat]) lands in `--out-dir` for human review. It
prints a PASS/FAIL table and **exits non-zero on any failure**. Hang-safe: a hard
120s overall timeout always closes the browser and exits. Requires the dev
`playwright` package + `npx playwright install chromium`.

Today's run **reports real failures** at the mobile width — that's the *known,
parked* responsive debt (the screens haven't been migrated yet), and that backlog
is exactly what the suite is meant to track. The suite running + exiting cleanly
(non-zero on findings is correct) is the deliverable; driving the count to zero is
the FE-migration work.

- Optional later: `expect(page).toHaveScreenshot()` visual-regression baselines
  (higher maintenance) once the UI churn settles.
- Until then, every UI build also gets a **chat-aware review lens** (does each
  overlay use `--chat-width`? does each new tab clone an exemplar + reuse
  `shared/`?).
# Card-edge accents

Colored full-height card-edge rails are not a frontend primitive.

- Standard content cards use a neutral one-pixel outline.
- Status cards use existing text, badges, dots, icons, opacity, or a restrained whole-border/surface tint. Red is never decorative.
- Compact repeated identity rows may use a small marker beside the identity only for stable, useful grouping. The marker must agree with the visible group color, be `aria-hidden`, and must not change density or text alignment.
- Selected surfaces use a full border/surface tint. Keyboard focus uses the shared visible focus ring.
- Warning/error banners use an icon, semantic text, a one-pixel tinted outline, and restrained surface tint—not a thick edge rail.
- Neutral structural guides remain valid for nesting, blockquotes, tables, charts, resize boundaries, and navigation. Do not globally prohibit `border-left`.

Do not add a generic “colored rail” component or variant. Any new edge accent must first be classified using the audit in `docs/ui/card-edge-accent-audit.md`.
