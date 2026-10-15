# Statement Import — one match engine (unified match) — Acceptance Criteria

The QA contract for **Phase 2** of RFC
`.claude/rfcs/2026-07-29-one-match-engine-instructions-as-policy.md` (§D3 the two views, §D8
Frontend, Risk R3): the server becomes the single source of truth for "these might be the same
transaction". The browser stops re-deriving pairs (`weakResolved`, `twinPick`,
`explicitTwinHolds`, `consumedTwinIds`, `weakChangeOptions` are deleted), row flags gain the
`"weak"` band and a server-computed `alternatives` list, and `ledger_check` stops emitting
`twin_eligible` / `rows[].statement_twin`.

IDs are stable for `make qa-e2e`. Design spec: the `ui-designer` wireframe + region spec for
this change (Decide step of `ImportModal`, no new tab, no new page heading).

**Relationship to `statement-import.md`:**
- **SI-55** (weak band derived from `ledgerCheck.rows[].statement_twin`) is **SUPERSEDED** by
  `SIUM-WEAK-*` — the band now arrives on the row flag.
- **SI-63** (match picker driven by `ledgerCheck.twin_eligible`, with client steal/swap) is
  **SUPERSEDED** by `SIUM-ALT-*` — the option list is the flag's `alternatives`, and a pick is
  an ordinary `matchOverride`.
- **SI-57 / SI-52** ledger-only case taxonomy is **AMENDED** by `SIUM-LEDGERONLY-*`: the
  `claimed` case and its "resolve it there first" dead end are gone; only `booked` and
  `orphan` remain.
- **SI-46** filter ownership is **AMENDED** by `SIUM-CHIP-WEAK`: weak rows no longer live in
  `Needs you`. They belong to the outcome partition (`New`, or `Will link` once linked) and are
  additionally reachable through a transient, non-partitioning `Possible matches` lens chip —
  the same category of chip as the existing `Off by` lens.
- SI-33, SI-47, SI-48a, SI-50, SI-56, SI-59, SI-61, SI-62, SI-64, SI-69–SI-80 are **unchanged**
  and must stay green.

**AMENDED 2026-07-31 — the `Not the same` control is REMOVED (owner).** Its only distinct effect
was releasing the matched ledger entry back onto "In your ledger, not on this statement"; that
behaviour is now carried by the weak band's own `Import` decision, because on a maybe-match
"Import" (keep both) *is* the assertion that the two are not the same thing. Consequences:
`SIUM-WEAK-DISMISS` → `SIUM-WEAK-RELEASE`, `SIUM-WEAK-UNDO` → `SIUM-WEAK-RELINK`, and
`SIUM-COMMIT-WEAK-DISMISS` → `SIUM-COMMIT-WEAK-IMPORT`. The release is **weak-band only** — a
strong/review row's `Import` leaves its entry claimed. ADR
`2026-07-31-weak-import-releases-the-entry`.

---

## Fixtures the harness needs

- **F-INCIDENT** — the MoMo shape: a statement containing `+₫500,000` top-up rows, and a ledger
  holding unrelated `₫500,000` expenses plus a `kind=transfer` entry ("Transfer to HO KINH
  DOANH …") inside the statement period.
- **F-DRIFT** — the settled-drift shape: a booked estimate `−S$13.71` in the ledger and a
  settled `−S$14.07` row on the statement, same account, ≥1 shared description token.
- **F-ALT** — a weak-band row whose flag carries ≥2 `alternatives`.
- **F-CLEAN** — a statement with only strong matches and new rows, no weak flag at all.
- **F-BOOKED** — a ledger entry created by an earlier import of the same external reference,
  whose statement row is an L0 duplicate in this preview.

---

## The incident regression (why this change exists)

### SIUM-INCIDENT-TOPUP
- GIVEN fixture **F-INCIDENT** previewed to the Decide step
- WHEN I read the `+₫500,000` top-up row
- THEN it shows **no** ledger match line of any band — no "Ledger", no "Maybe", no decision chip
  other than `Import` — and its expanded body offers no partner to link
- AND the unrelated `₫500,000` expense rows and the "Transfer to HO KINH DOANH …" entry are all
  listed under **"In your ledger, not on this statement"**
- AND opening that section and expanding the transfer entry shows the orphan explanation, never
  a claim that a statement row already matched it.

### SIUM-DRIFT-CARD
- GIVEN fixture **F-DRIFT** previewed to the Decide step
- WHEN I locate the `−S$14.07` statement row
- THEN it renders as a **weak-band pair row** carrying the suggestion line
  "Maybe · <ledger note> · settled 2.6% off", a `Possible match` chip, and the decision chip
  `Import`
- ~~AND the `−S$13.71` ledger entry is **absent** from "In your ledger, not on this
  statement"~~ **(superseded 2026-07-31)** — at the weak band's `Import` default the entry is
  **present** there; see `SIUM-WEAK-RELEASE`
- ~~AND expanding the row reveals a working `Same one — link` control and a `Not the same`
  control.~~ **(superseded 2026-07-31)** — expanding reveals the three-way segmented control
  (`Same one — link` / `Import` / `Leave out`) and **no** `Not the same` control anywhere on the
  row, collapsed or expanded.

### SIUM-NO-DEADEND
- GIVEN any preview in this suite
- WHEN I expand every entry under "In your ledger, not on this statement"
- THEN the string **"A possible match is already linked above — resolve it there first"** never
  appears anywhere in the DOM, and no entry offers a "Show match ↑" button.

### SIUM-NO-TWIN-WIRE
- GIVEN any preview in this suite
- WHEN the `POST /api/statements/preview` response is inspected
- THEN it contains no `twin_eligible` key and no `rows[].statement_twin` key
- AND the Decide step still renders every weak card, every picker option, and the complete
  ledger-only list from `flags[].band` / `flags[].alternatives` / `ledger_check.rows` alone.

---

## The weak band — visibly weaker, answered by its own decision

### SIUM-WEAK-QUIET
- GIVEN fixture **F-DRIFT** on the Decide step, with a strong-band row also present
- WHEN I compare the two collapsed rows side by side
- THEN the strong row carries the green `Link` decision chip and its solid `⇄ Ledger …` match
  line, while the weak row carries the neutral `Import` decision chip, a **muted** (not amber,
  not green) `Possible match` chip, and a **dashed, dimmed** `? Maybe …` suggestion line whose
  amount is neither bolded nor drift-coloured
- AND the weak row is **not** numbered, **not** in the `Needs you` queue, and does not raise the
  `Needs you` chip count.

### ~~SIUM-WEAK-DISMISS~~ — SUPERSEDED 2026-07-31 by **SIUM-WEAK-RELEASE**
> ~~GIVEN the weak row from **SIUM-DRIFT-CARD**; WHEN I click `Not the same` on its suggestion
> line; THEN the suggestion line disappears, the row reads as a plain `Import` row with no match
> line, AND the `−S$13.71` ledger entry **appears** under "In your ledger, not on this statement"
> with its count incremented by one, AND the coverage/amounts verdict lines update in place.~~
>
> The `Not the same` control is gone. Its release is now the meaning of `Import` on a weak row,
> so it fires at the band's own default instead of behind an extra click — the row no longer
> stops being a weak pair card, which is what makes the release reversible.

### SIUM-WEAK-RELEASE
- GIVEN the weak row from **SIUM-DRIFT-CARD**, at its `Import` default (no interaction)
- THEN the row keeps its `Possible match` chip and `? Maybe …` suggestion line — it is still a
  weak pair card, and it carries **no** dismissal control
- AND the `−S$13.71` ledger entry **is listed** under "In your ledger, not on this statement",
  under the ORPHAN copy, with the ordinary `Open` / `Link to a new row` / `Delete` / `Leave it`
  actions and never a "claimed above" dead end (`SIUM-NO-DEADEND` still holds)
- AND the verdict's second question names it ("N ledger entr… from this period …"), while the
  first question still reports the statement row as `imported new`
- AND the same is true after an explicit click on `Import`.

### SIUM-WEAK-RELEASE-BAND
- GIVEN a **strong**-band and a **review**-band row, each matched to a ledger entry
- WHEN I set either row's decision to `Import`
- THEN its matched entry stays **absent** from "In your ledger, not on this statement" — the
  release is weak-band only, because those bands' `Import` is a deliberate "keep both", not a
  statement that the two are different things.

### SIUM-WEAK-RELINK (replaces ~~SIUM-WEAK-UNDO~~, superseded 2026-07-31)
- GIVEN the weak row from **SIUM-WEAK-RELEASE**
- WHEN I click `Same one — link`
- THEN the ledger entry leaves the ledger-only list and the verdict lines return to the
  all-covered reading
- AND clicking `Import` again releases it again — the panel is a pure function of the row's live
  decision, so there is no dismissal state to undo and nothing to get stuck in.

### SIUM-WEAK-LINK
- GIVEN the weak row from **SIUM-DRIFT-CARD**, expanded
- WHEN I click `Same one — link`
- THEN the decision chip becomes `Link`, the `After link` projection shows one record with
  `S$13.71 → S$14.07` and the badges `cleared ✓` / `reference attached`
- AND the row moves into the `Will link` chip population
- AND the `−S$13.71` entry stays out of "In your ledger, not on this statement".

### SIUM-WEAK-LEAVEOUT
- GIVEN the weak row from **SIUM-DRIFT-CARD**, expanded
- WHEN I click `Leave out`
- THEN the `After` panel reads that nothing books and yours stays `S$13.71`, the amount in the
  collapsed row renders struck/dimmed, and the row joins the `Left out` chip population
- AND (2026-07-31) the `−S$13.71` entry stays **out** of "In your ledger, not on this statement":
  `Leave out` is not a rejection — nothing double-books precisely because that entry already
  covers the row, so its claim stands. Only `Import` releases it.

### SIUM-CHIP-WEAK
- GIVEN fixture **F-DRIFT** (at least one unlinked weak row)
- WHEN the Decide step renders
- THEN a `Possible matches N` chip is present in the filter row, `aria-pressed="false"`
- AND clicking it shows exactly the weak-band rows and nothing else, statement order preserved
- AND after every weak row is **linked** the chip disappears and the filter falls back to `All`
  rather than stranding an empty list (amended 2026-07-31: linking is now the only thing that
  resolves the possibility — with the dismissal gone, a row left at `Import` keeps its
  suggestion, and therefore stays in the lens)
- AND with fixture **F-CLEAN** the chip never renders at all.

---

## The match picker — driven by server `alternatives`

### SIUM-ALT-PICKER
- GIVEN fixture **F-ALT**, the weak row expanded
- WHEN I click `Change match`
- THEN a listbox opens (`aria-haspopup="listbox"`, `aria-expanded="true"`) naming every entry
  the server sent in `flags[].alternatives`, each as `MM-DD · note · amount (same amount |
  X.X% off)`, the current partner marked `aria-selected="true"` and hinted `· current`
- AND the option count equals the length of the response's `alternatives` array for that row —
  no option is added, dropped, dimmed, or re-ordered by the client.

### SIUM-ALT-PICK-REPOINTS
- GIVEN the open listbox from **SIUM-ALT-PICKER**
- WHEN I click a non-current option
- THEN the popover closes, the row's suggestion line, the expanded `In your ledger` column, the
  evidence text, and the `After …` projection all re-point to the picked entry
- AND the row's decision becomes `Link`
- AND `Confirm & book` submits `existing_id` equal to the picked entry's id (assert on the
  request body, and on the booked result: the picked ledger row is the one that changes).

### SIUM-ALT-BACK
- GIVEN a row re-pointed by **SIUM-ALT-PICK-REPOINTS**
- WHEN I click `Back to Lucius's pick`
- THEN the row returns to the server's own proposed partner everywhere (suggestion line, ledger
  column, projection) and the `Back to Lucius's pick` control disappears.

### SIUM-ALT-NONE
- GIVEN a weak or strong row whose flag carries an empty `alternatives` array
- WHEN I expand it
- THEN **no** `Change match` control renders (never a dead control that opens an empty list)
- AND the row's other controls (`Same one — link` / `Import` / `Leave out`) still work
  (2026-07-31: `Not the same` was in this list until the control was removed).

### SIUM-ALT-SEARCH
- GIVEN a `Change match` listbox with more than four options
- WHEN I type a fragment of one entry's note into the search field
- THEN only matching options remain listed, and picking one behaves as **SIUM-ALT-PICK-REPOINTS**
- AND pressing `Escape` closes only the popover — the import modal stays open with every row
  decision intact.

---

## Ledger-only entries — the two-case collapse

### SIUM-LEDGERONLY-BOOKED
- GIVEN fixture **F-BOOKED**
- WHEN I expand its entry under "In your ledger, not on this statement"
- THEN the explanation reads that the ledger already has this from an earlier import
- AND the actions are `Open`, `Link to a new row` (when linkable rows exist), `Delete`,
  `Leave it` — with no "Show match ↑".

### SIUM-LEDGERONLY-ORPHAN
- GIVEN any entry with no earlier-import copy
- WHEN I expand it
- THEN the explanation is the orphan copy ("A real card charge should appear on its own
  statement …") and the same four actions render
- AND clicking `Open` opens the real transaction drawer above the import modal; closing it
  returns to the Decide step with every row decision unchanged.

### SIUM-LEDGERONLY-LINK
- GIVEN an orphan entry and at least one unmatched `Import` statement row
- WHEN I click `Link to a new row` and pick that statement row
- THEN the entry leaves the ledger-only list, the chosen statement row becomes a
  "Linked by you" pair card defaulted to `Link`, and its `Unlink` control restores both sides.

### SIUM-LEDGERONLY-DELETE
- GIVEN an orphan entry
- WHEN I click `Delete`
- THEN the entry is really deleted (it does not return on re-expanding the section) and the
  ledger-only count decrements.

---

## Bands that must NOT move

### SIUM-STRONG-UNCHANGED
- GIVEN a strong-band row
- WHEN the Decide step renders and I expand it
- THEN it keeps the `✓ Will link` treatment, the green `Link` decision chip, the solid
  `⇄ Ledger` match line, the `Link / Import / Leave out` radiogroup, and its `After link`
  projection — byte-for-byte the SI-69/SI-70/SI-75 behavior.

### SIUM-REVIEW-QUEUE
- GIVEN a review-band row and a weak-band row in the same preview
- WHEN I click the `Needs you` chip
- THEN the review row renders as a numbered, fully-expanded queue card with its receipt and
  `Undo`, and the weak row is **absent** from the queue
- AND the `Needs you` count equals the number of undecided review/needs-eyes rows only.

---

## The Will-link bulk bar — it counts what it mutates

### SIUM-BULK-SCOPE
- GIVEN a `Will link` population that is MIXED — some rows a confident engine match, others
  linking because of a probable match, a link I made, or one Lucius routed
- WHEN I click the `Will link` chip and then any button on the bulk bar above the list
- THEN it moves exactly the **N rows the list beneath it renders** (kind filter included) and no
  others — no row outside the active filter has its decision overwritten, and the confident-
  engine-match subset is not treated as the whole population.
- Pinned: `ImportModal.bulkBar.test.tsx` ("a bulk button moves exactly the rows rendered beneath
  it" derives N from the rendered rows and counts the mutation in the same test).

### SIUM-BULK-COPY (re-scoped 2026-07-31)
- GIVEN the `Will link` chip is active
- THEN the bar is **buttons only** — no headline, no muted note, no count of its own; the
  `Will link` section header directly beneath is where the count lives
- AND the buttons read `Link all` · `Import all` · `Leave all out` — the app's one word per
  action, in the same order as each row's own decision control.

### SIUM-BULK-NO-DEADEND
- GIVEN the `Will link` chip is active
- WHEN a bulk `Import all` / `Leave all out` empties that population
- THEN the filter falls back to `All` so the untouched siblings stay visible; a narrower bulk
  (a kind filter is on, rows remain under the chip) leaves the filter where it is.

---

## Commit

### SIUM-COMMIT-WEAK-MERGE
- GIVEN the weak row linked by **SIUM-WEAK-LINK**
- WHEN I click `Confirm & book`
- THEN the commit succeeds, the done screen reports the row as linked (not created), and the
  ledger holds ONE `S$14.07` transaction carrying the original category/notes.

### SIUM-COMMIT-WEAK-IMPORT (was ~~SIUM-COMMIT-WEAK-DISMISS~~, re-keyed 2026-07-31)
- GIVEN the weak row at its `Import` decision (**SIUM-WEAK-RELEASE**) — no dismissal step
- WHEN I click `Confirm & book`
- THEN the ledger holds TWO transactions (`S$13.71` untouched and a new `S$14.07`), and the done
  screen's ledger-check count includes the untouched entry.

### SIUM-COMMIT-STALE-REFUSED
- GIVEN a preview whose statement record has been re-previewed since (a stale decision payload)
- WHEN I click `Confirm & book`
- THEN the modal shows the existing "re-run the preview" error inline and books nothing — no
  silent partial commit.

---

## Empty / loading / error

### SIUM-LOADING
- GIVEN a file chosen on the Choose step
- WHEN the read runs
- THEN the Read step's phase line advances and clamps on the final phase; no weak chrome, no
  ledger-only section, and no picker is rendered before the preview resolves.

### SIUM-EMPTY-NO-ROWS
- GIVEN a file the server parses to zero rows
- WHEN the Decide step renders
- THEN the existing `EmptyState` "No transactions found in this statement" renders with its
  "Try another file" action, and no filter chips, weak chrome, or ledger-only section render.

### SIUM-EMPTY-NO-LEDGER-DIFF
- GIVEN a statement where every ledger entry in the period is accounted for (2026-07-31: which
  now means *no unlinked weak row* either — a weak row at `Import` releases its entry, so this
  case needs fixture **F-CLEAN**, not merely an empty `ledger_check.rows`)
- WHEN the Decide step renders
- THEN the "In your ledger, not on this statement" section does not render at all (no empty
  section box, no zero badge) and the verdict states the clean result.

### SIUM-EMPTY-NO-PERIOD
- GIVEN a statement with no printed period
- WHEN the Decide step renders
- THEN the quiet "cross-check unavailable" note renders instead of a ledger-only section, and
  no weak chrome implies a check that did not run.

### SIUM-ERROR-OPEN
- GIVEN a ledger entry that has been deleted elsewhere since the preview
- WHEN I click `Open` on it (or on a weak row's ledger name)
- THEN the inline "Couldn't open — try again." error renders in place and the modal keeps
  working — no crash, no silent no-op.

### SIUM-ERROR-DELETE
- GIVEN a ledger-only entry whose DELETE fails
- WHEN I click `Delete`
- THEN the inline "Couldn't delete — try again." error renders and the entry stays listed.

---

## Responsive & a11y

### SIUM-RESPONSIVE
- GIVEN the Decide step at 1440px, 1024px and 390px, in light and dark theme, with the Lucius
  panel open AND closed
- WHEN a weak row, a strong row and the ledger-only section are all on screen
- THEN there is no horizontal page overflow and no console error
- AND at 390px the weak suggestion line stacks under the statement line at full row width and
  wraps rather than clips, the `Change match` control is at least 44px tall (amended 2026-07-31:
  `Not the same` was named here until the control was removed), and the expanded comparison
  stacks `On the statement` → drift rail → `In your ledger` → decision controls → `After …`.

### SIUM-MOBILE-SHEET
- GIVEN the Decide step at 390px
- WHEN I open a matched ledger entry from a weak row's ledger name
- THEN the transaction detail renders as the bottom sheet (ADR
  `2026-07-27-txn-drawer-bottom-sheet-on-mobile`) above the import modal, with its grab handle
  and sticky close
- AND closing it returns to the Decide step with every row decision and override intact.

### SIUM-A11Y-KEYBOARD
- GIVEN the Decide step
- WHEN I traverse a weak row with the keyboard only
- THEN the row disclosure toggles on `Enter`/`Space`, the three decision radios and
  `Change match` are focusable with a visible focus ring (amended 2026-07-31: `Not the same` was
  named here until the control was removed), the listbox exposes `role="listbox"` with
  `aria-selected` on the current option, and `Escape` closes only the popover
- AND every control reachable by mouse in this file is reachable by keyboard.
