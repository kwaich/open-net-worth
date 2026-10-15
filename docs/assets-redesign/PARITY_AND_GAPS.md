# Assets tab — parity and gaps (owner review)

Companion to `docs/adr/2026-09-06-assets-tab-faithful-port.md`. The prototype
(`frontend/design_handoffs/assets_handoff/prototype/assets_tab/`, local only) is the spec; this
file lists everything the shipped tab does NOT do exactly as the prototype, and everything the
prototype's source contains that `main.jsx` never mounts. Nothing here was dropped silently.

## Prototype features shipped with a real-data seam (behaviour identical, data real)

| Prototype | Shipped |
|---|---|
| `ASSETS` mock list | `GET /api/assets` — `owned_assets` store; value = newest mark |
| `LOANS` mock (`Lark Home Loan`) | every non-archived liability account of a loan type, ledger-computed balance |
| `CLASSES` cash / investments cards + `OTHER_DEBT` + `worthTotals()` | the server `summary` block (net worth service + portfolio analytics) — the same figures Overview shows |
| `addCustomCategory()` (in-memory) | `POST /api/assets/categories`, persisted in `owned_asset_categories` |
| photo = `loremflickr` URL + PNG fallback | user upload → attachment; `<img src="/api/uploads/{ref}/image">`; no photo → category icon |
| `docs: ['file.pdf']` names + a `Switch` "Lucius can read" (local state) | attachments with `lucius_readable` persisted and honoured by the read tools |
| `marks[].t` (mock years) | derived from ISO mark dates |
| "Bought on" free text `Mar 2019` | a real full-date input, stored as ISO `YYYY-MM-DD` |
| `dispose()` removes from list | posts the proceeds as ONE real transaction into the chosen cash account, archives the asset (history kept) |
| "Describe it to Lucius" → `Lucius Tab.html` with `sessionStorage` seed | the real dock prefill `onAskLucius('Add an asset: ')` |
| `.mask` privacy class | the app's `.money` / `body.hide-amounts` convention |
| Toast (`setTimeout` 2600 ms) | same, with `mb-anchored` so it clears the phone tab bar |

## Present in the prototype SOURCE but never mounted by `main.jsx` — not ported

- `AssetTable` (dense table view, `register.jsx`) and the `.mk-density` toggle — no density
  switch exists in the shipped header. Follow-up if the owner wants the table view.
- `FlowsTable` (`detail.jsx`) — "Money in & out" (carry cost / income from ledger transactions
  tagged to an asset). Needs the wireframes' "one nullable `asset_id` on transactions"; not in
  the hi-fi and not built. The detail sub-tabs are exactly the two mounted ones: Valuation
  history · Attachments.
- `ReviewModal`, `ReviewSettings`, review pips, and the "Needs a look" rail — deliberately
  retired with asset review scheduling. The current product has no asset-review cadence or
  linked Automation.
- **No document-upload control.** The Attachments sub-tab lists documents (name, added date,
  "Lucius can read") and the empty state invites you to add one, but the prototype has no
  button or drop zone for it. Shipped as designed: documents arrive through
  `POST /api/assets/{id}/docs` (Lucius / API); the photo thumbnail IS uploadable. Follow-up:
  an "Add attachment" affordance once the design covers it.
- `Rail`'s old `onReview` prop — retired with the review feature.
- CSS-only leftovers: `.as-sec*`, `.as-card-cat`, `.as-card-asof`, `.as-foot`, `.as-ev`,
  `.as-lines`, `.as-rv`, `.as-back`, `.as-auto`, `.as-bar-sp`, `.as-bar-side`, `.as-chip.warn`,
  `.as-cls-lines`, `.as-pip.live`, `.as-chart-lg i.dash`, `.as-dgrid`, `.as-body.norail` — pruned
  by the dead-class ratchet (`STRICT_DEAD=1 npm run lint:classes`).

## Design decisions the owner should confirm (from the wireframes' "would ask before building")

1. **Existing property / vehicle / collectible / other_asset ACCOUNTS are not migrated.** The
   hi-fi does not cover it. A user who already tracks a flat as an account AND adds it under
   Assets double-counts until they archive the account. Proposed follow-up: a one-time
   "convert to asset" action (balance → first mark, `last_reconciled_at` → as-of, note scanned
   for a loan) with an undo note.
2. **Ownership share** counts share-adjusted in net worth and shows gross on the card/detail —
   exactly the prototype's `shareOf()` + "Net worth counts your share only" help text.
3. **Collections** ("Gold bars — 300g") are one asset; no quantity field (the prototype has
   none). Splitting is a follow-up.
4. **Disposal auto-posts** the inflow (no review step) — the prototype's "Posts a real
   transaction" help text; the transaction is idempotent on retry.
5. **Automated marks?** Not built. Asset review reminders and their linked Automations are
   removed; `by: "automation"` remains valid mark provenance for a future explicit feed writer.
6. **Naming** — "Assets" as designed.

## Security notes for the owner (from the pre-ship security review)

- **Remove leaves attachments on disk.** "Remove from Assets" deletes the record only; a photo or
  document it referenced stays in the attachment store (safe direction, no surprise destruction)
  and remains listed under uploads. Follow-up: cascade behind a confirmation, or say so in the
  modal copy.
- **`run_sql_query` can read `owned_assets`.** The table holds user notes and facts (plate
  numbers, serials, policy numbers). It is deliberately outside `SECRET_TABLES`, consistent with
  the SQL boundary being the connection allowlist, so with SQL access enabled Lucius can read
  those columns regardless of a document's "Lucius can read" switch (which governs document
  CONTENT, not the asset row). Stated here so it is a known trade-off, not a surprise.
- **Analytics folds exclude owned-asset rows.** KPIs / gains / venues gate on instrument-bearing
  rows; the Assets summary owns owned-asset unrealized gain. Allocation and reachability still
  include the assets (banded property / commodity / other, illiquid).

## App features the design omits (kept, unaffected)

- Accounts of the four physical types still exist and still band via `asset_class`.
- Intel's "asset" vocabulary (instrument detail, `/asset/$instrumentId`, `asset_brief`) is a
  different concept; the new domain is named `owned_assets` everywhere to keep them apart.
