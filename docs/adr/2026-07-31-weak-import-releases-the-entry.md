# 2026-07-31 — Remove `Not the same`; a weak row's `Import` releases its matched ledger entry

**Status:** Accepted
**Owner:** the maintainer ("let's remove this not the same button")
**Touches:** `frontend/src/components/tabs/accounts/ImportModal.tsx`,
`frontend/src/components/tabs/accounts/statementImport.css`,
`docs/qa/acceptance/statement-import-unified-match.md`,
`.claude/rfcs/2026-07-29-one-match-engine-instructions-as-policy.md` (§D3 amendment (c))

## Context

On a **weak**-band ("POSSIBLE MATCH") row in the statement-import Decide step, two controls
produced the *same ledger write* and differed only in a reporting side effect:

| Control | Statement row | Matched ledger entry |
|---|---|---|
| `Import` | books as new | stays **claimed** — hidden from "In your ledger, not on this statement" |
| `Not the same` | books as new | **released** — reappears there (`weakDismissed` → `dismissedWeakEntries`) |

The release exists because `ledger_check.rows` is a pure set difference: the server subtracts any
entry a match flag claimed. RFC `2026-07-29-one-match-engine-instructions-as-policy` §D3
amendment (c) added the client-side re-insertion so that rejecting a weak pair could not leave a
real ledger entry marked as accounted-for by a statement that never contained it — the silent
under-reporting class that RFC exists to kill.

Deleting the button and leaving `Import` alone would have re-opened exactly that hole. Worse, the
hole was already half-open: the weak band's **default** action is `Import`, so an untouched weak
row already booked a second transaction while its partner stayed marked as covered. The dismissal
button only patched the case where the owner clicked it.

## Decision

**Delete the control; make the release a pure function of the weak row's own decision.**

`Import` on a weak suggestion *is* the assertion that these are not the same thing, so:

| Weak-band decision | Matched entry |
|---|---|
| `Same one — link` (merge) | claimed — it is linked |
| `Import` (new) | **released** onto the ledger-only panel |
| `Leave out` (skip) | claimed — nothing double-books *because* that entry covers the row |

Scope and shape:

- **Weak band only.** On `strong`/`review`, `Import` keeps its current meaning — those bands
  demand a deliberate answer, and releasing their entry off a default would be a different bug.
- **Derived, not stored.** `dismissedWeakEntries` is now computed per render from
  `pairBand(d) === 'weak' && verdictInputs[i].effect === 'new'`, keyed by the id the server chose
  and carrying the summary the server sent. Keying on the row **effect** (not the raw action)
  keeps it in lockstep with the commit ladder: an unrouted, pre-reconciliation or absorbed row
  never books as new and therefore never releases.
- **Reversibility comes for free.** With no dismissal state there is nothing to undo: switching
  the row to `Same one — link` re-claims the entry, switching back to `Import` releases it again.
  The row also stays a weak pair card throughout — the old dismissal collapsed it to a plain row,
  which is why it needed a bespoke `Undo`.
- **Still not a second matcher.** The re-insertion carries no tolerance, no scoring and no claim
  arbitration; it is the inverse of `checkRemoved`, unchanged in kind.

Deleted: the two `Not the same` buttons (suggestion line + expanded action row), `dismissWeak` /
`undoDismissWeak`, the `weakDismissed` state and its four reset sites, the `isWeakDismissed` /
`liveMatchFlag` indirection (now plain `matchFlag`), the dismissed-receipt line with its `Undo`,
and the `.imp3-arow-maybe-no` / `.imp3-arow-undo` / `.imp3-arow-undo-btn` CSS families with their
two media-query blocks (the `lint:classes` reverse pass ratchets on dead classes).

**Not touched, per the owner:** `canManualLinkRow` / `linkableDrafts` — the "Link to a new row"
picker still excludes weak rows.

## Consequences

- **A weak-matched entry is now listed as uncovered by default**, before any interaction. This is
  the intended, more honest reading — the app is about to create a second transaction and leave
  that entry untouched — but it is a visible change to the first frame of the Decide step, not
  only to an explicitly-rejected row.
- The `Possible matches` lens now empties only on **link**; previously a dismissal also removed a
  row from it.
- `SIUM-WEAK-DISMISS` → `SIUM-WEAK-RELEASE`, `SIUM-WEAK-UNDO` → `SIUM-WEAK-RELINK`,
  `SIUM-COMMIT-WEAK-DISMISS` → `SIUM-COMMIT-WEAK-IMPORT`; struck through and dated in
  `docs/qa/acceptance/statement-import-unified-match.md`, never deleted. New:
  `SIUM-WEAK-RELEASE-BAND` pins the strong/review exclusion.
- Regression-pinned in `ImportModal.threeState.test.tsx` (release at default, `Leave out` keeps
  the claim, relink round-trip, and the band clause over strong + review).

## Known gap deliberately NOT fixed here

The ledger-only section header's money subtotal is the **server's** `totals[0].uncovered_net`,
computed before any client decision. It already drifts from the rendered list in three existing
ways (a Delete, a manual link, a `Change match`), and a released weak entry is now a fourth —
count and subtotal can disagree. Correcting it client-side would mean summing entry amounts in
the browser across currencies, which is monetary math the repo puts in `services/` and an FX bug
waiting to happen. The right fix is server-side (have `ledger_check` report the weak-claimed
entries separately, or stop subtracting them and let the client claim on link) and is out of
scope for a control removal.

## Agent provenance

Implementing agent (Claude) made two calls the directive left open:

1. **The release fires at the band's default, not only on an explicit tap.** The alternative —
   releasing only when the user actively clicks the already-selected `Import` — would make the
   reconciliation panel depend on invisible state the owner cannot see or verify, which is the
   same category of lie the panel exists to prevent. The panel must reflect the decision the app
   is actually about to execute.
2. **`Leave out` keeps the claim.** Skipping a matched row is the classic "don't double-book"
   resolution and therefore asserts sameness, not difference; releasing there would report an
   entry as uncovered when the owner just said the ledger already has it.
