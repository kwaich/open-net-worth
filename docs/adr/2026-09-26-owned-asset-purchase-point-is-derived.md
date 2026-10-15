---
number: 2026-09-26
title: An owned asset's purchase point is derived from its price and date, never stored
status: Accepted
date: 2026-09-26
deciders: orchestrating Claude agent (solution-architect design) + repo owner
---

# An owned asset's purchase point is derived from its price and date, never stored

## Context

An owned asset's value is a series of dated valuations (`marks`). Until now, creating an asset
with a purchase price also *stored* a second mark — the price, dated `cost_date` or, with no date,
**today**, noted "entered on add". A walkthrough on 2026-09-26 showed what that shape costs:

- a price with no date became a valuation dated today (two same-day rows, a vertical chart line);
- editing the price or date later never touched that stored mark, so the history's purchase point
  and the dashed purchase line disagreed;
- a policy's "premiums paid" (the redesign maps it onto `cost`) would be stored as a historical
  value it never had;
- the same create also wrote a system fact `["Added", "Sep 2026"]` users then edited as their own,
  and a literal `"— none"` note.

Around it: share outside 0–100 was silently clamped; one loan could "secure" two assets; a sold
asset kept its loan link and so blocked the loan account's deletion; the Assets register computed
its composition client-side from unshared values ("Property 151%"); and a mistyped value could be
neither corrected nor removed. The owner's redesign (PRD `assets-create-edit-redesign`) requires
all of these fixed, plus correct/remove for values. `CLAUDE.md`: all money math in `services/`;
"an owned asset's value is its newest mark; identity edits never write marks".

## Decision

We will treat the purchase as **derived data** and tighten the valuation contract around it:

1. **Derived purchase point.** Stored `marks[]` hold valuations only. The purchase point is
   computed on read from `(category, cost, cost_date)` and exists only when `cost > 0`, `cost_date`
   is set and the category is not `policy`. It is projected into every series as
   `{id: "purchase", kind: "purchase"}`; stored marks project as `kind: "valuation"`.
2. **Value rule** (`services/owned_assets_valuation.py`). The value on a date is the newest point
   on or before it across valuations and the purchase point; a valuation outranks the purchase
   point on the same date; a purchase point dated after the newest valuation is excluded. So
   **today's value is always the newest valuation** — no purchase edit, category change or
   migration step can move today's net worth.
3. **Purchase-first rule.** From now on no valuation may be dated before `cost_date`, a changed
   `cost_date` may not fall after the earliest valuation or the sale, and a sale may not predate
   the purchase.
   Legacy rows that already break it stay editable (the check runs only on changed inputs).
4. **Recording appends, correcting replaces, removing deletes.** Update value always appends a new
   dated valuation. A mistyped valuation can be corrected in place (value/date/note; keeps `id`
   and `created_at`, stamps `edited_at`) or removed; the purchase point refuses both, and the last
   valuation refuses removal. This amends ADR 2026-09-06 Decision 4's "values are never
   overwritten"; "identity edits never write marks" stands.
5. **Share outside 0–100 is refused, never clamped** — at the service and, as defence in depth, at
   the store.
6. **A loan secures at most one live asset** (409 `loan_in_use`, naming the holder); disposing an
   asset clears its loan link.
7. **Composition and per-asset share value are server money math.** `summary.composition` is built
   from the same `ledger.net_worth()` breakdown as "Everything you own", share-adjusted and
   base-converted, with integer percentages that sum to exactly 100 (largest remainder). Each
   asset carries `share_value` in its own currency. Amends ADR 2026-09-06 Decision 7 for the rail.
8. **One error contract for `/api/assets`:** `{detail, code, field?, reason?, holder?}` — `detail`
   is a complete plain sentence shown verbatim by the UI and to Lucius.
9. **One Alembic data migration** (`202609261500_owned_asset_purchase_point_derived`) drops the
   create-time stored purchase mark (identified exactly: note "entered on add" stamped 1µs before
   its sibling), normalizes `source`/`"— none"` notes, strips the system "Added" fact, sets
   `edited_at`, re-mints a missing, duplicate or `"purchase"` mark id (correct and remove address
   a value by it), re-sorts, and releases loans held by sold assets; portable import applies the
   same normalization. The same rewrite runs over every checkpoint's owned-asset before-image
   (`write_log.before_payload`, keyed by checkpoint and seq): a restore replays those raw, so a
   checkpoint taken before the upgrade would otherwise bring the old shape back — the stored
   purchase copy read as a valuation, the "Added" fact, a sold asset's loan link. Every row and
   image is read and rewritten before anything is written, so one malformed row refuses the
   revision with nothing changed (fail-closed); an INFO line summarizes what changed.

## Consequences

### Positive
- The stale-purchase-point bug class is gone: there is nothing to keep in sync.
- Today's net worth is provably unaffected by the redesign, the migration, and any Purchase edit.
- A wrong value can finally be fixed; the Assets rail can never exceed 100%.
- Readable, field-addressed errors let every dialog keep the user's input and show the reason.

### Negative
- Past net-worth history **moves on purpose** for assets whose purchase was edited, cost and date
  added later, and policies (whose fabricated stand-in disappears) — documented in the changelog.
- A correction keeps no before-image (only `edited_at`); Lucius corrections are approval-gated and
  checkpoint-undoable, UI corrections come from an explicit edit form.
- The purchase-first rule can refuse a legitimate back-dated value; its message names the way out.
- One loan per live asset cannot represent cross-collateralised loans; tranche purchases (gold
  bought over time) cannot be modelled with one purchase point.
- The migration cannot tell a user fact literally reading `["Added", "<creation month>"]` or a note
  literally `"— none"` from the system's; both are normalized (`created_at` keeps the date).

### Follow-ups
- Lucius tools `correct_asset_value` and `remove_asset_value` (names final).
- `docs/INVARIANTS.md`, `docs/DATA_MODEL.md`, `CLAUDE.md` invariant line, product changelog.
- Formatting every asset amount by `a.currency` (pre-existing base-symbol shortcut) stays open.

## Alternatives considered
- **Store the purchase as a `kind: "purchase"` mark and re-sync it on every cost/date/category
  write** — rejected: every write path that forgets the sync reintroduces the bug; it weakens
  "identity edits never write marks"; category knowledge would leak into `db/` for import.
- **Newest point wins with kind as the tie-break** — rejected: a later purchase date would make
  the price today's value, so a Purchase edit could move net worth.
- **Valuations only, purchase point only before the first valuation** — rejected: changes past
  history for legacy rows and makes the chart disagree with the net-worth contribution.
- **Keep a before-image chain or audit table for corrections** — rejected: unbounded growth for a
  typo fix; approval + checkpoint undo + `edited_at` cover it.
- **Compute `share_value` in base currency** — rejected: an FX failure after a committed write
  would 503 a saved change and invite duplicate retries.

## Agent provenance

- **Decided by:** orchestrating Claude agent, on the solution-architect's RFC; owner-approved
  product decisions ("D", option 1, "go e2e").
- **Lane / branch:** `lane/assets-create-edit` (worktree `.claude/worktrees/lane+assets-create-edit`).
- **Alternatives weighed:** stored-and-synced purchase mark vs derived point; three value rules;
  audit chain vs `edited_at`; base vs native `share_value`.
- **Driving artifacts:** PRD `docs/prd/assets-create-edit-redesign.md`, RFC
  `.claude/rfcs/assets-create-edit-redesign.md`, the 2026-09-26 walkthrough evidence.
- **Why this way (one line):** derived data cannot go stale, and a value rule that can only ever
  report a real valuation as "today" keeps net worth fixed through every edit and migration.
