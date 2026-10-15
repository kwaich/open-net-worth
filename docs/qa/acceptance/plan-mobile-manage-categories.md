# Acceptance criteria — Plan tab mobile Manage-categories cards

The card-based mobile layout for the "Manage categories" modal (639.98px viewport width and
below, matching `useIsMobile()`'s own breakpoint), replacing the horizontally-scrolling 4-column
grid. Sibling change to [`plan-mobile-budget-cards.md`](plan-mobile-budget-cards.md) (the Budget
sub-tab's own table → card conversion) — same pattern, applied to the category manager instead.

**Feature**: Mobile card layout for "Manage categories" — one card per category, 2 stacked rows
(identity+kebab / kind+envelope), no horizontal scroll, no grid header. The standalone
"Hidden" toggle that used to make a 3rd row was removed as redundant — Hide/Unhide is reachable
ONLY via the kebab menu's own Hide/Unhide item now (same handler either way).
**Commit**: `065734a17` (fix(plan): mobile-friendly Manage categories modal — cards, not grid) +
`3f092b44a` (test(plan): E2E coverage for the mobile Manage-categories card layout) + this
branch's review-fixes follow-up (circular-import extraction into `CatMgrRow.tsx`, the
639.98px/0.5 consistency fixes, and the locked-category test) + a later follow-up that dropped
the redundant standalone Hidden toggle in favor of the kebab-only Hide/Unhide.
**Design**: TBD
**Component files**:
- `frontend/src/components/tabs/plan/categoriesManager.tsx` (desktop grid + mobile branch,
  `useIsMobile()`-gated, mirrors `Register.tsx`'s own `TxnRow`/`TxnCard` swap)
- `frontend/src/components/tabs/plan/CatMgrRow.tsx` (desktop grid row + the shared
  `ManagedCategory`/`EnvelopeRow`/`KIND_LABEL`/`kindPillClass`/`EnvelopeCell`/
  `CatMgrRowProps` pieces both layouts import — split out of `categoriesManager.tsx` to avoid a
  `categoriesManager.tsx` ↔ `CatMgrCard.tsx` circular import; mirrors `accounts/TxnRow.tsx`)
- `frontend/src/components/tabs/plan/CatMgrCard.tsx` (mobile card, same props as `CatMgrRow`)
- `frontend/src/components/tabs/plan/plan.css` (`@media (max-width: 639.98px)` block near the
  other `.pl-catmgr-*` rules)
- `frontend/src/components/tabs/plan/categoriesManager.mobileCard.test.tsx` (vitest: render
  branch against a mocked `matchMedia`, plus the locked/structural-category case)
- `frontend/qa/pl-catmgr-mobile-cards.mjs` (Playwright E2E: drives the real running app at a
  390×844 viewport through the actual mobile nav path — More → Plan → Plan settings → Manage
  categories — then re-verifies the same code at a desktop viewport)

Status is earned by evidence: every criterion clicked/driven at 390px width against the real
app, or asserted against the real DOM in the vitest suite. "It rendered" is not a pass.

---

## Layout & scroll

### CATMGR-MOBILE-NO-SCROLL
- GIVEN the "Manage categories" modal open at 390px viewport width
- WHEN the page loads and I scroll through the category list
- THEN there is NO horizontal scrollbar on `.pl-catmgr-table` or on the document as a whole, and
  every card fits within the viewport width without truncation or hidden content.
- **Verified**: `frontend/qa/pl-catmgr-mobile-cards.mjs` measures `scrollWidth <= clientWidth`
  on both `.pl-catmgr-table` and `document.documentElement` at a 390×844 viewport — PASS.

### CATMGR-MOBILE-NO-GRID-HEADER
- GIVEN the modal open at 390px
- WHEN the list renders
- THEN `.pl-catmgr-table .pl-thead` (the 4-column grid header: Category/Kind/Envelope/Hidden)
  is NOT rendered at all — not hidden with CSS, absent from the DOM — and no
  `.pl-catmgr-row` grid rows render either, only `.pl-catmgr-card` articles.
- **Verified**: `pl-catmgr-mobile-cards.mjs` (0 `.pl-catmgr-table .pl-thead`, 0
  `.pl-catmgr-row:not(.pl-catmgr-addrow)`, ≥1 `.pl-catmgr-card`) and
  `categoriesManager.mobileCard.test.tsx` (same assertions against a mocked `matchMedia`) — PASS.

### CATMGR-MOBILE-CARD-STRUCTURE
- GIVEN a category rendered as a card on mobile
- WHEN viewing the card
- THEN it shows 2 stacked rows:
  1. Identity — color dot (or the locked/inert dot for a structural category) + category label
     (double-tap to rename), Kebab menu pinned to the right.
  2. Classification — the Kind pill (Expense/Inflow/Transfer/Investment) on the left and the
     envelope badge or italic "Not budgeted" on the right.
  There is no standalone "Hidden" row or switch on the card face — Hide/Unhide is reachable
  ONLY through the kebab menu (see `CATMGR-MOBILE-HIDE-TOGGLE` below); a duplicate control for
  the same action on the card face was removed as redundant.
- **Verified**: `categoriesManager.mobileCard.test.tsx` asserts `Taxable` and `Tax-free` are
  absent on mobile, and that `queryByText('Hidden')` and
  `.pl-tgt-switch` are both absent from the card — PASS.

### CATMGR-MOBILE-HIDDEN-DIM
- GIVEN a hidden category rendered as a card
- WHEN viewing the list
- THEN the card carries `.cat-hidden` and its opacity is dimmed to 0.5 — the same value every
  other hidden-dim rule in `plan.css` uses (`.pl-crow.cat-hidden`, `.pl-mcard.cat-hidden`), not
  an independently-chosen value.

## Card interactions

### CATMGR-MOBILE-KEBAB-MENU
- GIVEN a category card on mobile (not a locked/structural category)
- WHEN I tap the kebab (⋯)
- THEN a popover opens with the same actions and order as desktop: Rename, "Add to envelope…"
  (only for an unlinked, non-income category), Hide/Unhide, a divider, then Delete…
- WHEN I tap an action
- THEN it proceeds exactly as on desktop (same handlers, `CatMgrRowProps` is shared verbatim
  between `CatMgrRow` and `CatMgrCard`).

### CATMGR-MOBILE-RENAME
- GIVEN a category card
- WHEN I double-tap the label (or use the kebab's "Rename")
- THEN the label switches to an inline text input (`InlineEdit`); Enter commits, Escape cancels,
  matching desktop.

### CATMGR-MOBILE-HIDE-TOGGLE
- GIVEN an unlocked category card
- WHEN I tap the kebab and tap its Hide/Unhide item
- THEN a real `POST /api/categories/{id}/hide` (or `/unhide`) fires, the list reloads, and the
  kebab's own menu item label flips (Hide ↔ Unhide) to match the new state. There is no
  standalone switch on the card face — this is the ONLY way to hide/unhide a category on
  mobile, same handler (`onToggleHide`) as desktop's grid-row switch.
- **Verified**: `pl-catmgr-mobile-cards.mjs` opens the kebab, clicks Hide/Unhide, waits for the
  real POST via `page.waitForResponse`, confirms the menu item's label flipped, then clicks it
  again to leave seeded data untouched — PASS.

## Add-category form

### CATMGR-MOBILE-ADD-FORM
- GIVEN the modal open at 390px
- WHEN I tap "+ New category"
- THEN a `.pl-catmgr-card.pl-catmgr-addcard` shell appears: a color dot + full-width name input
  on row 1 (Enter adds, Escape cancels — the SAME shared keydown handler the desktop add-row
  uses), and the Kind select on row 2. The "Not budgeted" envelope text that
  desktop's grid shows (because the grid column must render something) is dropped on mobile —
  there's no envelope column to fill.

## Desktop regression

### CATMGR-DESKTOP-LAYOUT
- GIVEN the modal open at desktop width (e.g., 1280px)
- WHEN the page renders
- THEN `.pl-catmgr-table .pl-thead` renders the four current columns (count 1),
  `.pl-catmgr-row` grid rows render, and `.pl-catmgr-card` never appears — the mobile branch
  does not leak into desktop and no removed Taxable column remains.
- **Verified**: `pl-catmgr-mobile-cards.mjs`'s second half reloads the SAME app at 1280×900 and
  re-asserts all three — PASS. `categoriesManager.mobileCard.test.tsx` asserts the same with a
  mocked desktop `matchMedia`.

## Edge cases

### CATMGR-MOBILE-LOCKED-CATEGORY
- GIVEN a structural category (`STRUCTURAL_CATEGORY_KEYS`, e.g. the `split` sentinel)
- WHEN it renders as a card (it never reaches the manager's own list — `CategoriesManager`
  filters structural categories out of `all` before rendering — so this is exercised by
  rendering `CatMgrCard` directly, same approach `lucius.entityWiring.test.tsx` already uses
  for `CatMgrRow`)
- THEN the card shows the locked/inert dot (no color picker), and the kebab wrapper is present
  but `aria-hidden` + inert (`visibility:hidden`, `pointer-events:none`, `tabIndex=-1`) rather
  than a live menu — matching desktop's own locked-row pattern. Since Hide/Unhide now lives
  only inside the kebab, a locked category has no way to be hidden from this card at all (no
  switch fallback exists anymore); `.pl-tgt-switch` is absent from the DOM entirely.
- **Verified**: `categoriesManager.mobileCard.test.tsx` — PASS.

### CATMGR-MOBILE-LONG-NAME
- GIVEN a category or envelope with a long name
- WHEN rendered on mobile (390px)
- THEN the label truncates/wraps gracefully and does NOT force `.pl-catmgr-card` to overflow
  horizontally.

### CATMGR-MOBILE-EMPTY-STATE
- GIVEN a filter/search combination that matches zero categories
- WHEN the list renders on mobile
- THEN the existing `EmptyState` ("No categories match") renders — unchanged from desktop, no
  JS errors, no orphaned card markup.

### CATMGR-MOBILE-BREAKPOINT-CONSISTENCY
- GIVEN a viewport width between 639.98px and 640px
- WHEN `useIsMobile()` (JS, `matchMedia('(max-width: 639.98px)')`) and `plan.css`'s own
  `@media (max-width: 639.98px)` block are evaluated
- THEN they agree on which layout is "mobile" — no 1px window where the JS branch renders cards
  but the CSS still expects the grid (or vice versa). Both use the identical `639.98px` value.

---

**Flow is "passing" when:** every criterion is PASS at 390px width against the running app,
the desktop layout remains functional, `frontend/qa/pl-catmgr-mobile-cards.mjs` and the vitest suite
(`categoriesManager.mobileCard.test.tsx`, `categoriesManager.render.test.tsx`,
`categoriesManager.test.ts`, `lucius.entityWiring.test.tsx`) are all green, and any bugs found
are noted separately.
