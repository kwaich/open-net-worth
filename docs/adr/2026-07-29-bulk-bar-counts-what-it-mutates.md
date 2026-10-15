# 2026-07-29 — A bulk bar's population is the list it sits above, not a band it re-derives

**Status:** accepted (owner bug report — Meridian Bank import, 2026-07-29). The **copy** below (headline +
note) was removed on 2026-07-31 — see `2026-07-31-bulk-bar-is-buttons-only`. The **population
rule** (`setAllForRows` takes the rendered array) is current truth and unchanged.
**Where:** `frontend/src/components/tabs/accounts/ImportModal.tsx` (`setAllForRows`, the
contextual bulk-bar block), `frontend/src/components/tabs/accounts/accounts.css`
(`.imp-pair-bandhead`, `.imp-bandhead-lede`, `.imp-bandhead-note`)
**Tests:** `frontend/src/components/tabs/accounts/ImportModal.bulkBar.test.tsx` (7) ·
`ImportModal.threeState.test.tsx` (bulk-bar cases retargeted to the new copy)
**Acceptance:** `SIUM-BULK-SCOPE` / `SIUM-BULK-COPY` / `SIUM-BULK-NO-DEADEND` in
`docs/qa/acceptance/statement-import-unified-match.md`

## Decision

A bulk action bar takes the array of rows the list under it is rendering, and that ONE array
feeds all three of: the number in its label, the copy, and the mutation.

Concretely, `setAllInBand(band, action)` — which re-derived its own population from `band` — is
replaced by `setAllForRows(rows, action)`, and the bar passes `visibleStmt`: the exact
`{ d, i }` pairs rendered beneath it, kind filter included. There is nothing left to keep in
sync, so label ≠ action is no longer a state the component can reach.

The label was the half that changed. The action was already right: the bar sits above a list of
N and a user reads it as operating on those N.

Copy (owner's numbers): `✓ Will link — all 33`, with `21 matched automatically` as a muted
second line, and buttons `Link all` · `Import all` · `Leave all out`.

## Why

The bar read **"✓ Will link automatically — 21"** above a list of **33**, and its buttons acted
on all 33. The two halves were computed differently:

```js
// what the BUTTONS acted on
band === 'strong' ? effectiveChipKey(d, i) === 'strong' : pairBand(d) === band
// what the LABEL counted
pairBand(d) === 'strong' && effectiveChipKey(d, i) === 'strong'
```

`effectiveChipKey` returns `'strong'` for anything that ends up merging — a confident engine
match, an FX leg absorbed into its partner transfer, a manual link, a Lucius `force_match`, a
review-band match already resolved. `pairBand` returns `'strong'` only for the first of those.
On the owner's real Meridian Bank statement, `Import all as new` would have booked **33 duplicate
transactions** against a promise of 21, and `Skip all` would have silently discarded **12**
decisions the owner and Lucius had already made. Pre-existing since `736be5c2` (the handoff
port); the Phase 2 frontend pass only dropped a dead `'weak'` arm from the signature.

Three things followed from fixing the label rather than the action:

1. **"Will link automatically — N" cannot be true of a mixed population.** Only 21 of the 33
   matched automatically. The headline now counts the population the buttons move and the
   automatic subset is subordinate to it — a note, in muted weight, stacked underneath so the
   two counts cannot read as rivals. When the whole population IS automatic the note drops its
   number (`matched automatically`); when none of it is, the note does not render. Neither
   variant puts a second number where a user looks for the bar's scope.
   The remainder is deliberately left unattributed: it is *not* uniformly "you decided" — a
   review-band row is pre-decided by the profile's `import_review_default` at load, and a folded
   FX leg was decided by nobody. A subset claim is true; a partition claim would not be.
2. **The bar is scoped by the kind filter too.** `visibleStmt` is post-filter, so a bulk action
   taken while `Transfers` is selected moves the transfers only — previously it would have
   reached every linking row on the statement while showing a dozen.
3. **The button labels join the app's vocabulary.** `ImportModal` already states its own rule —
   *"One consistent word per action across the whole app: link, import, leave out"* — and the
   bar was the last surface saying "Merge"/"Skip"/"Import all as new". It now reads
   `Link all · Import all · Leave all out`, in the same order as every row's own decision
   control directly beneath it, so "the same word means the same thing" survives the jump from
   the bar to a row.

## What we did NOT do

**We did not narrow the action to 21.** The `Will link` chip shows 33, and the bar is the only
way to act on all of them at once; narrowing it would have left the owner with no bulk path over
the 12 rows Lucius and their own links produced — exactly the rows a bulk sweep is for. The 33
rows a bulk action reaches are still, individually, reachable one row at a time.

## Residual, accepted

An FX leg folded into its partner transfer is in the list, so it is counted and its
`matchAction` is written — but its outcome is decided by `isAbsorbedLeg` before `actionFor` is
consulted, so it keeps folding. `Import all` over a list containing one moves the other rows and
leaves that one under `Will link`, still labelled "Folded into transfer". Un-folding it is the
pair's own Include control and must stay an explicit act: it turns one transfer into two
separate transactions. Counting it (it is on screen) beats hiding it from the count.

## Agent provenance

Found and fixed while auditing the Decide step after an owner bug report. The bug was in the
half the report did not accuse: the instinct is to "fix the buttons to match the label", and
that would have shipped a bar that quietly refused to touch two thirds of what it sat above.
The general rule extracted: **a control that both counts and mutates must do both from one
array** — two predicates over the same rows is a defect waiting for the day they disagree.
