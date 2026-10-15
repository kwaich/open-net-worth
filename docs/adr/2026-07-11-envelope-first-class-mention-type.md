# 2026-07-11 — Envelope is a first-class mention type; row surface decides the type

**Status:** accepted (owner request, 2026-07-11: "is envelope supported in the Lucius mention
system, including suggestions and drag-and-drop? if not, fix it e2e", refined mid-review:
"if user drags a category row, it's a category; if it's an envelope, it must be envelope")
**Where:** `backend/services/mentions/` (types, _common, providers, resolve,
descriptors/singletons), `frontend/scripts/gen-entity-registry.mjs` (+ regenerated
`entityRegistry.generated.ts`), `frontend/src/components/tabs/plan/budget.tsx`,
`frontend/src/components/tabs/plan/categoriesManager.tsx`, `tests/test_mention_envelope.py`

## Decision

Add `envelope` (order 26 in the registry sequence, `order=25`, icon `◫`, tone `credit`) to
the mention registry as its own entity type, keyed by the budget row's **envelope id**
(`row.envelope_id` from `planning.budget_month`), with bespoke `search_envelope` /
`_resolve_envelope` overrides (class (d) singleton, same shape as `category`). Sub-decisions:

1. **A new type, not a widening of `category`.** ADR `2026-07-05-envelope-id-native` pinned
   the "category" mention to a real category's own identity — never a row's envelope id.
   That invariant stands untouched; the envelope (the budget ROW) is a different entity and
   gets its own type. `_resolve_envelope` returns the row's current-month slice (group, linked
   category count, assigned/activity/available/target/status, + `available_for_payment`/
   `underfunded` on a payment envelope); synthetic display rows (Unsorted/Unbudgeted, no
   `envelope_id`) are never envelope mentions.

2. **The surface decides the mention type (owner rule).** A drag/right-click/harvest from an
   ENVELOPE row emits `envelope:<envelope id>`; from a CATEGORY row it emits
   `category:<category id>` — never mixed:
   - **Plan budget table** = envelope rows (the column header literally says ENVELOPE): EVERY
     row emits `envelope:` refs, linked or not. The first cut kept `category:` refs on linked
     rows for byte-compatibility; the owner rejected that on sight (a migrated commitment
     envelope with a linked category dragged as "CATEGORY", reading as the wrong entity).
   - **Manage categories dialog** = category rows: each row now carries
     `entityRefProps('category', id, label)` + `draggable` (paused while renaming so the
     InlineEdit keeps text selection) — previously this surface emitted no entity refs at all.
   - Rows are `draggable` even when LOCKED (bot-managed commitment/card-payment envelopes;
     the structural `split` row): reorder DnD is gated separately (grip + `rowOver`/`rowDrop`
     only on unlocked rows, guarding on the module `DRAG` state a mention drag never sets).

3. **The `category` type covers its FULL domain, not just budget rows.** The Manage-categories
   surface lists income/transfer/investment kinds and unlinked dormant expense categories —
   none of which have a budget row — so `search_category`/`_resolve_category` gained a
   category-STORE leg: search appends store categories not already emitted as budget rows
   (sublabel = kind; the structural `split` sentinel excluded); resolve falls back to store
   identity (`label`/`kind`/`budgeted=False`) instead of `not_found`. Budget-row payloads are
   byte-identical to before.

4. **Envelope search covers ALL non-archived envelopes.** With sub-decision 2 the Plan screen
   harvest is envelope-only and the manager harvest is category-only, so backend search is
   where both types stay reachable for the same underlying money ("@Groceries" surfaces the
   envelope row AND the category identity, in separate type-labeled picker groups — same
   accepted overlap precedent as holding/instrument). Type synonyms:
   envelope/envelopes/pot/pots.

5. **Chips tint with the entity's own display color (owner follow-up, same session).** The
   `color` plumbing existed but was hard-gated to `type === 'account'` at SIX points
   (`dragRightClick.parseRef`, `MessageBubble`, `Chip.renderGlyph` + its no-SVG fallback,
   `Autocomplete` picker icon, `Composer.handlePick`, and `MentionComposer.makeChip` — the
   composer's inline chip builder, whose marker/text-glyph branches ignored color entirely);
   all six now accept ANY type's `isSafeCssColor`-validated color, the drag ghost dot falls
   back to `data-entity-color`, the Plan/manager rows emit their row color, and the backend
   `MentionRow` contract gains an optional `color` (emitted for envelope/category/account
   rows) so remote picker rows tint too. Transaction chips keep direction tint precedence.

6. **Mention payloads are SCALARS-ONLY (owner bug report: "Target [object Object]").**
   `_resolve_envelope`/`_resolve_category` emit the target's AMOUNT via `_target_amount`,
   never the raw target-spec dict (which rendered as `[object Object]` in the detail
   popover — a pre-existing category bug the envelope resolver had inherited). Defense in
   depth: `MentionDetail`'s resolve/cache ingress now drops any non-scalar value, so a
   resolver that forgets to flatten (e.g. `budget_month`'s `totals`, still emitted for the
   model fence) can never paint `[object Object]` again.

## Why

Zero-category envelopes (commitment pots, Credit Card Payments envelopes, named savings pots)
were invisible to the whole mention system, and category-linked Plan rows presented as the
wrong entity when dragged. The registry is designed for exactly this extension (descriptor +
regenerated client bundle; the client is type-agnostic), so the fix is one descriptor, one
provider/resolver pair, and attribute-level row changes rather than any client plumbing.

## Consequences

- `@`-suggestions, picker search (incl. type synonyms), drag-into-dock, right-click "Ask
  Lucius", and server-side resolution work for every envelope AND every category (all four
  kinds, budgeted or not); the mention type always matches the surface dragged from.
- REGISTRY count 26 (order + coverage tests re-pinned); `entityRegistry.generated.ts`
  regenerated from the live backend (verify the generator printed "loaded N descriptor(s)
  from backend" — a silent fallback would drop post-Phase-2b types). Category descriptor
  gains `kind`/`budgeted` fields for the store-fallback payload.
- Verified: full backend suite + frontend vitest green, `make lint` clean, `npm run build` +
  `lint:classes` clean, plus a real-book (isolated data dir, no mocks) E2E smoke over
  search/resolve/format for both types (evidence in the shipping commit).

## Agent provenance

Built by Claude (Fable 5) directly from the owner's chat request, compressing the
brainstorm→spec cycle into this ADR because the change follows the registry's established
extension pattern end-to-end. The first cut's "linked Plan rows keep category refs"
conservatism was overruled by the owner mid-review and replaced with the surface rule
(sub-decision 2); the Manage-categories gap (sub-decisions 2/3) was owner-reported in the
same session.
