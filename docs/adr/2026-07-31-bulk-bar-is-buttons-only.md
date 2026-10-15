# 2026-07-31 — The Will-link bulk bar is buttons only; the invariant it carried moves to a test

**Status:** accepted (owner directive, 2026-07-31) — supersedes the **copy** half of
`2026-07-29-bulk-bar-counts-what-it-mutates`; its **population** rule stands unchanged
**Where:** `frontend/src/components/tabs/accounts/ImportModal.tsx` (the contextual bulk-bar
block), `frontend/src/components/tabs/accounts/accounts.css` (`.imp-pair-bandhead`)
**Tests:** `frontend/src/components/tabs/accounts/ImportModal.bulkBar.test.tsx` (5)
**Acceptance:** `SIUM-BULK-SCOPE` / `SIUM-BULK-COPY` (re-scoped) in
`docs/qa/acceptance/statement-import-unified-match.md`

## Decision

The bar renders `Link all` · `Import all` · `Leave all out` and nothing else. The headline
`✓ Will link — all N` and the muted `N matched automatically` sub-line are gone, along with
`.imp-bandhead-lede`, `.imp-bandhead-note` and `.imp-bulkset` (its last use).

`setAllForRows(visibleStmt, action)` is **untouched**. The bar still mutates exactly the rows
rendered beneath it, kind filter included.

## Why

The `Will link` section header sits directly under the bar and already states the count, so the
headline was the same number twice — and the sub-line answered a question ("how many of these
did the engine find on its own?") the owner was not asking at the moment of a bulk action.

The two-day-old copy was not wrong; it was the careful repair of a bar that had promised 18 and
moved 37. Deleting it is safe **because the repair was never in the copy** — it was in
`setAllForRows` taking the rendered array. What the copy provided was an *observable* of that
repair: a number on screen that would visibly disagree the day the two halves drifted again.

So the observable moves into the test rather than evaporating. `ImportModal.bulkBar.test.tsx`
used to assert *label count == mutated count*; it now derives the expected count from the
rendered rows (`.imp3-review-list [data-draft-index]`) and asserts *rendered count == mutated
count*, which is the invariant the label was only ever a proxy for. Re-narrowing the bar's
population to `pairBand === 'strong'` — the exact original defect — fails 3 of its 5 cases.

## What we did NOT do

**We did not add a replacement count anywhere.** A second number next to the section header's is
what created the drift, and the section header is 40px below the buttons.

**We did not simplify `setAllForRows` back toward a band predicate** now that nothing renders its
count. That is the shape of the original bug, and it would no longer be visible on screen.

## Agent provenance

Scoped by the owner as "remove the label, keep the buttons". The non-obvious call was what to do
with the test that read the label: deleting it would have left a real, two-day-old money bug
unguarded, and the assertion it made (label == mutation) was reachable only through copy that no
longer exists. Rewriting it against the DOM rows kept the guard and made it stronger — it now
pins the property directly instead of through a string.
