---
title: by_date targets gain a contributions-measured "set aside by [date]" flavor (YNAB parity)
status: Accepted
date: 2026-07-10
deciders: Claude (implementer) + owner (the maintainer)
---

## Context

Our `by_date` budget target ("Custom" cadence) computed `needed = max(0, (amount - available_now)
/ months_left)` — it **always measured the current Available balance** and ignored
`needs_whole_amount`. That is exactly YNAB's **"Have a balance of $X by [date]"** savings goal:
spending from the envelope drops Available, so the target asks you to top it back up.

YNAB also offers a **second dated flavor** — **"Set aside $X by [date]"** — whose progress is
measured by your **cumulative contributions (Assigned)**, so spending from the envelope **never
sets you back**. Our engine had no way to express this, so under a dated goal the "I want to"
options all collapsed to the same Available-measured behavior. The owner asked to close the gap
and make it "100% matched."

The `needs_whole_amount` flag already selects the flavor for recurring targets and defaults to
`True` globally; `set_target` persists it only when `False` (True is the default, left unstored).
The book (owner inspection, 2026-07-10) holds **18 targets, all `monthly`, zero `by_date`** — so
there is no legacy dated target to preserve, and no per-type / backward-compat branching is
warranted.

## Decision

We will add a **contributions-measured flavor** to `by_date`, selected by the SAME
`needs_whole_amount` flag, keeping **one clean rule** (no per-type defaults, no migration):

1. **`needed_this_month`** — for `by_date`, `progressed = assigned_to_date if needs_whole_amount
   else available_now`, then `needed = max(0, (amount - progressed) / months_left)`.
   `assigned_to_date` is the **cumulative net Assigned to the envelope through the month**
   (`_assigned_to_date_map`, one file load via the new `BudgetAssignmentsStore.all_map()`). A
   spend reduces Available but not Assigned, so the set-aside flavor is spending-immune.
2. **`needs_whole_amount` keeps its single global default `True`** for every type (recurring AND
   by_date); `set_target` still persists only `False` to stay lean. The frontend always sends the
   flag explicitly (`tt === 'set_aside'`): "Have a balance of" / "Fill up to" send `False`, "Set
   aside" sends `True`. No `by_date`-specific default, no migration — the book has no dated
   targets to convert.
3. **The row** exposes `assigned_to_date` so the client shows accurate cumulative "Assigned so
   far"; the client `recompute` **trusts the server status/needed for `by_date` rows** rather than
   re-deriving from its per-month approximation (which would flip an ahead-of-pace goal to
   "underfunded" on a zero-assign month). The "I want to" picker surfaces the third option ("Have
   a balance of") only under Custom; "Set aside" under Custom is this new flavor.

## Consequences

**Positive**
- "Set aside $X by [date]" now matches YNAB exactly — spending never sets the goal back.
- One default everywhere — no per-type branching or legacy-only code in the money engine.
- The distinction is honest end to end: server math, persisted flag, and the editor preview all
  agree on whether a dated goal measures contributions or the balance.

**Negative / Follow-ups**
- Under Custom, "Fill up to" and "Have a balance of" both resolve to the balance flavor
  (`needs_whole_amount=False`) — cosmetically distinct but mechanically identical, so a Custom
  "Fill up to" reads back as "Have a balance of". Only "Set aside" is behaviourally distinct under
  Custom. Acceptable; matches how our engine already treated the two.
- With the single default, a `by_date` target created via API/AI **without** the flag defaults to
  set-aside (not balance). The UI always sends it, so this only affects headless callers, which
  should specify the flavor. A future book that already had unflagged dated targets would need a
  one-shot stamp — this book has none.
- `assigned_to_date` is a whole-history fold per `budget_month` render (one file load, O(months x
  envelopes) in memory) — negligible at personal-book scale.

## Alternatives considered

- **Keep by_date balance-only, don't offer "Set aside" under Custom** — rejected: the owner
  explicitly wanted YNAB's three dated options and the spending-immune behavior.
- **Reuse "assigned this month" (like recurring set-aside) instead of cumulative** — rejected: a
  dated goal is one total across many months; this-month Assigned can't express "already ahead of
  pace", so an ahead goal would wrongly read underfunded on a zero-assign month.
- **Per-type default (by_date -> False) + `Optional[bool]` persistence to protect legacy dated
  targets** — built first, then **rejected** (owner, 2026-07-10: "don't pollute the code with
  backward-compat logic only for me"): the book has zero `by_date` targets, so the special-casing
  guarded nothing. Reverted to the single global default.

## Agent provenance

- **Decided by:** Claude (implementer) on branch `feature/ledger-tracking-start`, at the owner's
  request to make dated "Set aside" targets "100% matched" to YNAB, then simplified at the owner's
  direction to drop backward-compat logic.
- **Alternatives weighed:** see above.
- **Driving artifacts:** owner requests 2026-07-10 ("let's do it! just to make it 100% matched";
  then "don't pollute the code with backward compatibility logic only for me — just update the
  data"). Owner inspection confirmed the book has no `by_date` targets, so no data update was
  needed.
- **Why this way:** the smallest change that adds the missing spending-immune behavior with a
  single, uniform default and no legacy-only branches — verified with unit tests on the
  spending-immunity, cross-month cumulation, and default-flavor properties.
