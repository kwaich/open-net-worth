---
title: Batch-aware dedup — books-close promotion, and three narrow owner calls that bound it
status: Accepted
date: 2026-07-16
deciders: orchestrating agent (Claude) design session with repo owner, 2026-07-16
---

# Batch-aware dedup — books-close promotion, and three narrow owner calls that bound it

## Context

`.claude/rfcs/batch-aware-dedup.md` (design session, 2026-07-16) gave the dedup engine a
batch-level reasoning step it never had: `matching.match_batch` can now promote an assigned,
mutually-exclusive, wordless review-band pair to `strong` when the batch is a *complete*
statement — the soundness argument being that a real charge in-window MUST be printed on a
complete statement, so an uncovered ledger entry whose only exact match is one row cannot be a
different purchase (a genuine collision would print too, and would compete for the same row,
failing exclusivity). This power is new and needs boundaries, or it over-promotes into cases
the engine has no business being confident about. Three narrow scoping questions came up during
the design session and were each decided on the spot (RFC "Open questions" §1–3); this ADR is
the decision record for those three, plus the promotion mechanism they bound, since none of them
is obvious from the code alone and a future agent extending `match_batch` to a new surface would
otherwise have to relitigate all three.

## Decision

We will bound books-close promotion (and its downstream consumers) with three rules, each
answering one open question from the RFC:

1. **Promotion requires a `source` difference (RFC §3, `_promote_books_close`).** A same-source
   self-pair — two rows the SAME ingestion channel wrote (e.g. two rows from the same agent
   run) — never promotes, even when otherwise the unique exact-amount pairing in-window. Cheap
   to check (`row.source != candidate.source`) and closes a pathological case: a batch
   completeness claim is about the STATEMENT'S coverage of real-world events, not about an
   ingestion channel accidentally emitting two rows that happen to tie out — those two rows
   never independently corroborate each other's existence. Implemented directly in
   `services/matching.py::_promote_books_close` (landed with the engine itself, ahead of this
   ADR — recorded here for completeness since it is exactly this design session's call #3).
2. **`bulk_add_transactions`'s pre-flight batch pass defaults to abort-and-report, not
   book-and-queue (RFC §4, Phase 3).** When `match_batch(context=BatchContext(complete=False))`
   over a pasted/dictated list finds strong matches, the tool does NOT book them — it reports
   `already_recorded: [{index, canonical_id, summary, score}]` so the caller (Lucius) tells the
   user "N of these are already in your ledger — I'll link instead of duplicate" and re-submits
   with per-row `decisions`. `force=True` is the unchanged escape hatch that books everything
   regardless. A pasted/dictated list is never a "complete record of a window" claim, so this
   default is orthogonal to books-close promotion itself — it governs what a REVIEW-band or
   already-strong (from a non-promoted source, e.g. an exact external-id echo) match does when
   there is no per-row review UI in the loop. (Phase 3 — not yet wired; the tool's own pre-flight
   pass is a separate lane.)
3. **A non-statement link keeps the CANONICAL's date; only reference + cleared are absorbed (RFC
   §4, open question #2).** `ledger_service.apply_import_merge` gained a `facts` parameter
   (`services/statements/decisions.py`, `services/ledger.py`) with three modes: `"statement"` —
   the incoming row's date AND settled amount win (the statement-flow link action, every band);
   `"reference_only"` — a later phase's non-statement consumer (a bulk-add/connector-sync link
   whose incoming row carries no fact authority over an existing, possibly hand-curated,
   canonical) absorbs ONLY the external-id reference and `cleared`, touching neither date nor
   amount; `"amount_only"` — the pre-existing (pre-RFC) default, kept byte-identical for every
   caller that doesn't opt in, preserving the settled-amount-wins-but-date-never-moves behavior
   the import-dedup-merge design already shipped. In every mode, the canonical's LABELS —
   category, notes, payee, tags, splits — are never touched (they simply never ride in the
   merge patch). Only the `"statement"` mode is wired to a caller today
   (`decisions.py::_apply`'s merge loop, unconditionally, all bands); `"reference_only"` is a
   built, tested primitive with no caller yet — it exists so the Phase 4 consumer
   (`match_review.review_created_rows` rebuilt on `match_batch`) has a home to land in without
   re-opening this design question.

## Consequences

### Positive

- One additive parameter (`facts`) instead of a second merge function — a single "merge choke"
  stays the ONE place fact-authority is decided, matching the "one home for matching logic"
  principle the parent RFC itself is enforcing on the dedup engine.
- The default (`"amount_only"`) is byte-identical to pre-RFC behavior, so every un-migrated
  caller (the legacy `/api/import/merge` manual-mapping path) is provably unaffected —
  regression-pinned in `tests/test_import_merge.py`.
- The three rules are each independently cheap and independently testable (source-diff: engine
  unit tests in `tests/dedup/test_match_batch.py::test_same_source_never_promotes`; facts modes:
  `tests/test_import_merge.py`), so a future phase extending promotion to a new surface inherits
  working guardrails instead of an implicit assumption.

### Negative

- `bulk_add_transactions`'s abort-and-report default (call #2) is a real UX behavior change for
  that tool once Phase 3 lands — a caller relying on today's silent book-and-queue-everything
  needs `force=True` to keep it. Deferred cost, not paid yet (Phase 3 is unbuilt), but recorded
  now so the eventual implementer doesn't have to re-derive the default from the RFC prose.
- `"reference_only"` ships with no caller — a small amount of unused-but-tested surface area
  until Phase 4. Accepted: building the primitive alongside `"statement"` (they're one function,
  one review pass) is cheaper than re-opening `apply_import_merge` a second time later, and the
  RFC's own phase plan already commits to a Phase 4 consumer.

### Follow-ups

- Phase 3: wire `bulk_add_transactions`'s pre-flight pass to `match_batch(complete=False)` +
  the `already_recorded` report + per-row `decisions` param (RFC Implementation plan, step 7).
- Phase 4: rebuild `match_review.review_created_rows` on `match_batch` and give it its own
  `facts` mode decision (`"reference_only"` is the RFC's proposed default — not re-litigated
  here, just proposed).

## Alternatives considered

- **No `source` check on promotion** — rejected: cheap to add, closes a pathological self-pair
  case with no cost to the common case (two different real sources always still promote).
- **`bulk_add_transactions` books-and-queues strong matches by default (today's shape for
  review-band matches)** — rejected: a strong/promoted-adjacent match on a PASTED list (not a
  statement) still deserves a human look before silently linking into an existing row; abort-
  and-report keeps the tool's existing all-or-nothing validation-phase shape instead of adding a
  partial-book path.
- **A non-statement link also takes the incoming row's date** (mirroring `"statement"` mode) —
  rejected: a pasted/dictated list or a connector-sync row has no completeness claim and no
  authority over a canonical the user may have hand-entered/curated; overwriting its date on a
  weaker signal risks silently moving a real transaction's date on a mere fuzzy match.
- **A second merge function instead of a `facts` parameter** — rejected: `apply_import_merge`
  already IS the merge choke `decision_commit` calls; a parallel function would fork the "what
  wins on merge" logic the way the RFC's own problem statement complains `ledger_twin` forked
  matching logic. One function, one parameter, three modes.

## Agent provenance

- **Decided by:** orchestrating agent (Claude) design session with the repo owner, 2026-07-16
  (the same session that produced `.claude/rfcs/batch-aware-dedup.md`); implemented in the
  Phase 2 lane (statement flow + three-state review UI).
- **Lane / branch:** `feat/batch-aware-dedup`.
- **Alternatives weighed:** see "Alternatives considered" above — mirrors RFC "Open questions"
  §1 (bulk-add default), §2 (non-statement link date authority), §3 (source-diff on promotion).
- **Driving artifacts:** `.claude/rfcs/batch-aware-dedup.md` §"Open questions" and
  §"Merge semantics: facts vs labels"; `docs/superpowers/specs/2026-07-16-three-state-review-spec.md`
  (the UI consumer of the `"statement"` facts mode); supersedes the promotion-adjacent parts of
  `docs/adr/2026-07-13-multi-account-statement-routing.md` decision #31 (`ledger_twin`, deleted).
- **Why this way:** each of the three questions is a NARROW scope boundary on a genuinely new
  engine capability (batch completeness reasoning) — deciding them once, on the record, is
  cheaper than three separate future agents re-deriving the same boundary from RFC prose (or
  worse, three different agents drawing the boundary three different ways).

## Addendum (2026-07-16): five owner-decided fixes from the post-ship review

A second design conversation with the owner the same day (2026-07-16), triggered by three
incidents surfaced while actually reconciling real statements against this feature once it
shipped: the **Alex Tan false pair** (a PayNow income and an unrelated expense a week apart —
verified live at S$1,200.00, income 06-27 vs expense 07-04, two different accounts — were
proposed as a transfer pair; two single-leg rows that merely tie out on amount are not
evidence of one movement once the dates drift that far), the **hidden installment pair** (a
deliberately-categorized income row — a real, already-classified inflow — got silently
re-considered as the raw internal leg of a transfer candidate purely because its amount and
account-shape happened to match; a categorized row is a human's own classification and should
never be re-litigated by a heuristic without being asked), and an **orphan-storm** in the
ledger-check panel (a batch of "no statement row matches it" leftovers with no way to actually
look at what they were before deciding whether to remove them — the panel's own copy asked the
user to "check where it came from" while giving them no way to do so from that screen). All
three are instances of the same root cause the parent RFC already names: matching evidence
that is technically present but not actually strong enough for the weight the UI or the engine
put on it. Five fixes, each independently scoped and tested:

1. **Transfer-pair FORMATION gets a max gap (`TRANSFER_PAIR_MAX_GAP_DAYS = 2`).** The engine's
   TRANSFER_PAIR branch in `_classify` (`services/matching.py`) now refuses to propose a pair
   at all once the two candidate dates are more than 2 days apart — this is the direct fix for
   the Alex Tan false pair. Scope is deliberately narrow: this gates FORMATION only (two
   independent single-leg rows proposed as the two legs of a brand-new transfer). It does NOT
   touch `_classify_transfer_echo` (a single leg settling into a transfer that ALREADY
   exists in the ledger) — an echo leg legitimately posts days after the transfer's own
   booking date, and capping that too would have broken real, previously-working matches
   (pinned: `test_echo_three_day_gap_still_matches`). 2 days, not 0, because the existing
   `test_known_route_keeps_full_score`/scenario-07 fixtures already relied on a confirmed-route
   pair 2 days apart reaching STRONG — the fix could not silently regress a behavior the engine
   already shipped and tested.
2. **A categorized single-leg row never scores STRONG as a transfer-pair leg.** Same branch:
   if either side of a proposed pair carries a non-null user `category`, the score is capped
   at `SCORE_STRONG - 0.05` (review, never auto-eligible) and the reason string appends
   "(categorized leg — review)" — the direct fix for the hidden installment pair. Rationale:
   an already-categorized income is a deliberate human classification of where money came
   from; a genuinely mis-booked internal transfer leg is typically raw/uncategorized by
   construction (nobody categorizes their own transfer as "Salary" by accident as often as a
   real distinct payment gets categorized that way). The row may still surface for review — a
   human may confirm it really was a transfer — it just never silently auto-links.
3. **Honest pair-formation label + outcome copy.** `FLAG_LABEL.transfer_pair` changes from
   "Matches an existing transfer" (a factual lie for formation — no transfer exists yet) to
   "Opposite legs — one transfer?" (a question, matching what the engine is actually
   proposing). Investigating `decision_commit`'s merge path for a transfer_pair-classified row
   surfaced that `apply_import_merge`'s kind-match gate (`kind == draft.get("kind")`) can NEVER
   fire for a transfer_pair merge, because the two legs are opposite kinds (expense vs income)
   by construction — so merging one does NOT rebuild a transfer and does NOT rewrite date or
   amount; it only marks the existing row cleared and absorbs the statement row's reference for
   future dedup, and the statement's own row is never booked as its own transaction. The
   outcome line for this case (§7.1's `outcomeLine`) was rewritten to say exactly that instead
   of falling through to the generic date/amount-diff copy, which would have shown a
   date-re-stamp or amount-change fragment the merge never actually performs.
4. **The ledger-check panel's boundary bucket is inspectable, not just counted.** Boundary rows
   (dated near the statement's printed period edge — `_BOUNDARY_DAYS = 3` either side) already
   carried the full `_existing_summary` projection on the wire; the panel only ever showed a
   count. The summary line is now a collapsed-by-default expandable section (its own toggle,
   independent of the main panel), grouped client-side by `ledgerCheck.period` into "dated
   after the close" vs "dated in-period, near the close," each row rendered with the same date
   · note · account/category/source · amount detail as a core row — no twin, no verdict, no
   Remove (owner: "surface, don't conclude").
5. **Every panel entry opens the drawer; case D's copy is honest about what it can't rule out;
   case D also gets a direct Delete (owner amendment, mid-build).** The direct fix for the
   orphan-storm: every ledger-check entry's name (core cases A–D and the new boundary rows) is
   now a link that opens the SAME `TxnDetail` drawer the register uses, so a user facing a pile
   of "not on this statement" leftovers can actually look at each one before acting —
   inspect-then-decide instead of guess-then-remove. Case D's copy is position-aware and, since
   any row within `_BOUNDARY_DAYS` of the window edge already sorts into the boundary bucket
   (#4) instead of reaching case D, the only case that can actually occur here is mid-window —
   so the copy states that plainly ("a real card charge should appear on its own statement...")
   rather than hedging for a boundary case that structurally cannot happen at this call site.
   Mid-build, live-testing against his own book, the owner reversed the original "case D never
   gets a Remove" call for a Delete-specifically: drawer-per-row was too slow to clean six
   phantom agent-booked duplicate rides. Case D now gets a "Delete" button (deliberately NOT
   labeled "Remove," to read as a considered action on an unrelated orphan rather than "clean up
   this known dupe") wired to the SAME `removeCheckRow` danger/confirm/busy/failure choke cases
   A/B already use — no parallel delete path. This is NOT a reversal of the original data-loss
   safety fix (an unconditional Remove on every leftover, regardless of relationship to the
   statement): that fix was about REMOVING THE WRONG ROW BY DEFAULT before the user had looked;
   requiring the drawer first solved the wrong half of the problem (friction, not safety) once
   the drawer link existed on every case-D row right next to the button — the user still has to
   deliberately click Delete on a row they can now trivially inspect first. Boundary rows (#4)
   are UNCHANGED by this — still pure visibility, no action button of any kind; "surface, don't
   conclude" is a different guarantee than case D's "you decided, after looking (or not)."
   Separately investigated in the same pass: a live "Couldn't open — try again." on a panel row
   (root-caused, not reproduced firsthand — no code defect found). Panel rows correctly carry
   the ledger transaction's own `id` (never an external_id) through to
   `GET /api/transactions/{id}`, matching every other drawer-opening path in this file
   byte-for-byte. `merge_transactions` (`services/ledger.py`) tombstones the ABSORBED row
   (`deleted=True` + `merged_into`) on every merge; the ledger-check panel is a point-in-time
   snapshot cached client-side and is NEVER re-fetched live (by the same "the commit-time guard
   re-derives every match server-side" design this feature already relies on for SI-50's
   drawer). A panel row whose transaction gets merged away by OTHER activity (e.g. a concurrent
   dedup-review pass) between the preview and the click will correctly 404 — the inline
   "Couldn't open" message is the intended graceful-failure behavior for that race, not a bug;
   retrying can't resurrect a genuinely gone row, but the panel never crashes or silently
   no-ops. A test exercising a PANEL row's drawer-open (success AND failure) already existed
   from item #5's own build, verifying the id threads through correctly (`SI-52`).

### Consequences

- Positive: closes three concrete, owner-verified false-positive/UX-gap incidents with
  independently testable, narrowly-scoped fixes — none of the five touches the other four's
  code path, so they were each fail-first tested (engine items) or test-updated (UI items) in
  isolation.
- Negative / accepted: the formation gap (#1) and categorized-leg cap (#2) are both
  review-band-widening changes — a real transfer pair that happens to post more than 2 days
  apart, or whose leg the user happened to categorize, now needs a manual Merge instead of
  auto-linking. Accepted: silently mis-pairing two unrelated transactions (destructive,
  hard to notice) is worse than asking once (safe, undoable).
- Follow-up (not built here): the RFC's own Phase 5 items (OFX/MT940/CAMT completeness
  claims, connector-sync completeness, L3+policy auto-link for strong bands) are unaffected by
  this addendum and remain flagged follow-ups.

### Agent provenance (addendum)

- **Decided by:** orchestrating agent (Claude) design session with the repo owner, 2026-07-16 —
  a follow-up conversation to the one that produced the base ADR above, triggered by real
  reconciliation incidents against the shipped feature.
- **Lane / branch:** `feat/batch-aware-dedup`.
- **Driving artifacts:** owner-reported repros (Alex Tan false pair — S$1,200.00 PayNow income
  2026-06-27 vs an unrelated expense 2026-07-04, 7-day gap, pinned as a fail-first test before
  the fix landed; hidden installment pair; ledger-check orphan-storm); this ADR's own base
  decision #3 (source-diff on promotion) as the precedent for "narrow, testable scope rules on
  a powerful-but-new matching capability."
- **Why this way:** each fix is the smallest change that closes its specific incident without
  widening scope onto the surfaces this package explicitly excludes (`match_review.py`,
  `statements/decisions.py`'s flag plumbing, `ai/tools`) — consistent with the base ADR's own
  "narrow scope boundary, decided once" philosophy.

## Second addendum (2026-07-16): counterpart naming + targeted jump; case-B UI descoped

A third round of the SAME day's live-testing (owner actively reconciling against the
in-flight build) surfaced one more concrete failure in the ledger-check panel: pair entries
(cases A/B/C) referenced "that row" / "the table above" without ever naming WHICH row —
verified against a real drift case (ledger `06-19 −14.16` paired with a statement row
`06-18 −14.63` — different date AND amount) where the user genuinely could not find what the
copy was pointing at. `_annotate_statement_twins` (`services/ledger_diff.py`) already computes
and attaches `description`/`amount`/`date`/`row_index` on every twin; none of it reached the
UI. Three changes were proposed; only the first two shipped:

1. **Name the counterpart on every twin-bearing entry (cases A/B/C).** A new, dedicated
   second sub-line — `Statement row: MM-DD · "‹description›" · ‹signed amount› — ‹drift›` —
   renders under the existing explanation on any row carrying `statement_twin`, computing a
   drift percentage client-side when `exact` is false (`|twin.amount − |row.amount|| /
   |row.amount|`) or reading `"same amount"` when true. ADDITIVE, not a rewrite of the
   existing per-case explanation text (which still describes WHAT KIND of situation the row is
   in) — this new line's sole job is WHICH row, in one crisp, consistent format across all
   three cases instead of three different half-descriptions.
2. **"Show row ↑" (case C) becomes a targeted jump, not a bare filter switch.** The twin's
   `row_index` (the statement's own row index) resolves to its table draft via
   `ImportPreviewDraft.rowIndex` (the SAME field the FX-pair partner lookup already uses
   elsewhere in this file — no new plumbing); the table filter switches ONLY when the target
   is actually hidden by the current chip (no flicker when it's already visible), then the
   exact row scrolls into view and flash-highlights for ~2s (`imp-row-flash`, a one-shot CSS
   `@keyframes` fade — deliberately NOT a hand-rolled `prefers-reduced-motion` query, since
   `index.css`'s existing global rule already collapses every `animation-duration` to ~0 for
   reduced-motion users). A twin with no resolvable `row_index` falls back to the original
   filter-only jump (`goRows('needs')`) — never a dead click on malformed/legacy data.
3. **DESCOPED before any code was written.** The original ask also included giving case B
   (unflagged create) two in-panel pair-actions — "Keep yours" (skip the statement row) and a
   relabeled "Remove yours" — plus lightweight per-row skip-exclusion state threaded through
   `confirmImport`'s decision payload and the dynamic counts. Investigation DID confirm the
   premise (case B's existing copy, "…or Skip it in the table above," is false: an unflagged
   plain-create table row renders NO action controls at all — every Skip/Merge/Import-new
   control in the table is gated behind `matchesExisting(d)`, `d.pair`, or `d.needsEyes`, none
   of which a genuinely unflagged row carries). The owner pulled this item before build started:
   a bigger unification is being decided that would move weak/drift twin matches INTO the table
   itself as a third row tier, which would delete case B from the panel altogether — building
   panel-specific buttons for a case about to be redesigned away would be wasted work. Case B
   keeps its plain "Remove" button (shared with case A, unchanged) and gains ONLY item 1's
   counterpart-naming line. The false-copy finding is recorded here so the eventual case-B
   redesign starts from a confirmed bug, not a re-investigation.

### Consequences (second addendum)

- Positive: both shipped items are pure UI/read-side changes (no new backend fields — the data
  was already on the wire) with a real regression-test surface (drift-%/exact-amount copy;
  filter-switch-only-when-hidden; flash-then-clear timing; graceful fallback with no
  `row_index`).
- Negative / accepted: leaving the OLD per-case explanation text in place alongside the new
  counterpart line means case A in particular now states the twin's date/description/amount
  TWICE, in two different phrasings (inline prose + the new crisp line) — accepted rather than
  risk an unrequested copy rewrite of text that wasn't itself flagged as wrong; a future pass
  can trim the inline duplication once the format has been live for a while.
- Follow-up (intentionally not built): item 3 (case-B pair-actions) is deferred to whatever
  lands from the weak/drift-tier-into-the-table unification the owner is still deciding; this
  addendum's investigation finding (case B's Skip-copy is currently false) should feed directly
  into that redesign rather than being patched twice.

### Agent provenance (second addendum)

- **Decided by:** orchestrating agent (Claude) live-testing session with the repo owner,
  2026-07-16 — the third round of feedback the same day, mid-build (item 3 was descoped via a
  correction sent WHILE this addendum's items 1–2 were being implemented, before any item-3
  code existed).
- **Lane / branch:** `feat/batch-aware-dedup`.
- **Driving artifact:** a reported bug — ledger `06-19 −14.16` vs statement `06-18 −14.63`
  (different date AND amount), the exact drift case the panel's old copy gave no way to locate.
- **Why this way:** ship the two items with a clear, narrow, already-fully-specified shape;
  decline to build UI for a case (B) the owner is actively deciding to redesign out of
  existence — the same "don't widen scope past what's asked, don't build what's about to be
  thrown away" discipline the rest of this package already follows.

## Third addendum (2026-07-16): twin matcher — engine-claimed rows excluded, wording ranks + gates the drift tier, 1:1 exclusivity

A reported bug (two screenshots): `_annotate_statement_twins` (`services/statements/ledger_diff.py`)
paired the uncovered "Anthropic Claude API" entry (06-19, −14.16) with the statement's unrelated
"fp*Kestrel Eats Singapore SG" row (06-18, −13.70) instead of the equally-close ANTHROPIC row
(06-18, −14.63) — a row already engine-claimed by its own near-duplicate match was still offered as
a twin candidate, and the drift tier had no wording signal to break the amount/date tie. Fixed with
three narrow rules, all confined to this display-layer matcher (`services/matching.py` untouched): a
statement row already resolved to its own ledger entry (`near_duplicate`/`transfer_pair`/
`transfer_leg_echo` with a real `existing_id`) is no longer a twin candidate at all; a non-exact
(5%-drift) pairing now REQUIRES ≥1 shared wording token (case-insensitive alphabetic, length ≥4)
between the entry's note and the row's description/payee, while an exact-amount pairing stays
wording-blind (the BUS/MRT precedent, unchanged); and one statement row now backs at most one
entry — every eligible pairing is ranked (exact-first, then more shared tokens, then the tighter
date gap) and assigned greedily across the whole uncovered set, so a losing entry falls to its next
free eligible row instead of stealing the best-ranked entry's twin. Regression-pinned:
`tests/test_multi_account_import.py::test_ledger_check_twin_matcher_prefers_the_true_twin_over_an_engine_claimed_row`
(the exact repro) plus one focused unit test per rule. `docs/qa/acceptance/statement-import.md`'s
SI-41/48/51/52 describe the case-classification/display layer downstream of whichever row wins the
twin slot — reviewed, none falsified by this change, left as-is; SI-53's own worked example (ledger
−14.16 vs statement −14.63) already described this exact pairing as the intended outcome.

## Fourth addendum (2026-07-16): ranking now sees pre-cap fidelity — the swapped-Anthropic repro

A reported bug (two screenshots): the engine itself (`match_batch`, not the display-layer twin
matcher above) CROSS-LINKED two same-day statement rows — `-14.59`/orig `10.95` USD and
`-14.62`/orig `10.97` USD — to the WRONG ledger twins (swapped). Both rows amount-match BOTH
ledger entries under the same-day tolerance, and the review cap (`min(score, SCORE_STRONG -
0.05)`, `_classify_wordless_gap`) flattened the TRUE pair's exact F22 original-amount fingerprint
and the FALSE pair's merely-0.18%-off settled amount to the identical `0.75` — so
`_assign_batch_matches`'s claim key, `(-score, date_gap, row_index, alt_rank)`, fell through a tie
on score AND date_gap (same day) straight to raw ledger-pool list order. Fix: a `Candidate.fidelity`
field (`backend/services/matching.py:132`, 0.0 == exact — the F22 original-amount closeness
`_originals_align` already computed when both sides carry one, else settled-amount closeness;
`_amount_fidelity`, line 800) is now the SECOND key everywhere candidates are ranked: the global
claim key becomes `(-score, fidelity, date_gap, row_index, alt_rank)` (`_assign_batch_matches`,
line 1079), and both per-row candidate sorts (`check()`, line 880, and `_classify_batch_pairs`)
become `(-score, fidelity, date_gap)` via the shared `_date_gap` helper (line 820). Band/threshold/
cap semantics are UNCHANGED — these pairs still land review, never auto — only WHICH candidate
wins a capped tie changed. Regression-pinned:
`tests/dedup/test_match_batch.py::test_original_amount_fingerprint_pair_is_not_cross_linked` (the
exact repro, fail-first verified against the unfixed tree), `::test_settled_amount_closeness_alone_resolves_the_same_swap_shape`
(no F22 fingerprint at all), `::test_fidelity_never_overrides_score` (score still dominates), and
`tests/dedup/test_matching_engine.py::test_check_ranks_the_truer_amount_evidence_first_after_a_capped_tie`
(the same fix on the single-row `check()` path, a separate sort call site from `match_batch`).

## Fifth addendum (2026-07-16): three-column after-import projection + the commit guard accepts twin-derived merges

The pair-reconciliation table spec (`docs/superpowers/specs/2026-07-16-pair-reconciliation-table-spec.md`)
shipped with one owner amendment and one backend correction, both recorded here:

1. **Three-column row anatomy (owner amendment, supersedes the spec's §6 two-column body).**
   Every MATCHED pair row renders `STATEMENT | YOUR LEDGER NOW | AFTER IMPORT` — the third
   column is a LIVE projection of what the ledger will hold for this purchase after commit,
   recomputed instantly when the row's action chips flip. The projection is a pure function
   (`importHelpers.afterImportProjection`) that mirrors `apply_import_merge`
   (`facts="statement"`) EXACTLY, kind gate included: merge → ONE record (existing labels,
   statement date+settled amount as old→new deltas, original charge gap-filled only when
   missing, cleared ✓ + reference); a transfer target (echo) or a cross-kind pair
   (transfer_pair by construction, or a cross-kind weak twin) stays byte-whole on date/amount
   ("linked ✓" / "cleared ✓ · reference linked"); skip → the existing entry as-is ("yours
   stays as-is"); new → TWO stacked records with an honest "2 records" badge (the
   anti-double-booking honesty feature). Deliberately NOT re-derived from `outcomeLine`'s
   prose — the projection is data (records with delta fields), pinned against the backend's
   real outcomes in `importHelpers.afterImport.test.ts` so a future `apply_import_merge`
   change that alters outcomes fails a named FE test instead of silently lying in the column.
   Acceptance: SI-62.
2. **The guard extension the spec's "zero backend change" claim missed.** A weak-band Merge
   sends an `existing_id` sourced from the ledger-check TWIN — an id the matching engine never
   proposed, which FIX 2's commit guard (this ADR's own security lineage) would refuse.
   `decision_commit` (`services/statements/decisions.py`) now has a SECOND acceptance path:
   `_twin_merge_pairs` re-derives the ledger-check twin annotation at commit time — the same
   `preview_ledger_check` on the same cached rows, reused not forked — and accepts a merge
   whose (row_index → existing_id) pairing matches it exactly (booked twins excluded — their
   statement row is an L0 dup, never a live merge). Derived lazily (only when a merge names a
   non-engine id) and FAIL-CLOSED (an underivable check — no window, or an exception — yields
   no pairs, so the merge falls to the standard refusal). A fabricated id matching NEITHER the
   engine verdict NOR the twin pairing still raises `ValidationError`, byte-unchanged victim —
   pinned fail-first in `tests/test_decision_commit_guard.py`
   (`test_weak_twin_merge_is_accepted_with_statement_facts` reproduced the guard's rejection
   against the unmodified tree before the extension landed;
   `test_fabricated_id_is_rejected_even_when_a_live_twin_exists` proves the acceptance is
   pair-exact, never "any uncovered row"). Twin-derived merges use the same
   `facts="statement"` semantics as engine-proposed ones. Nothing else backend-side changed.
3. **Build deviations worth recording** (implementer micro-decisions, spec otherwise followed):
   the weak-twin join additionally excludes `needs-eyes` drafts (their skip-unless-included
   safety default must not be overridden by weak's decisive `new`; keeps
   `weakRows ∩ eyesOnlyRows = ∅` so the §8 counting invariant stays disjoint) and FX-pair legs
   (their fold/echo controls need the partner-row context of the statement table); the
   client-side `claimed-elsewhere` case reads the DRAFT's own re-derived `matchesExisting`
   instead of the preview-stamped `statement_twin.row_flagged` (fresher, same meaning — the
   stamp stays on the wire for `StatementsSection`'s own panel); ledger-only Delete stays the
   immediate confirmed API call (no staged-delete queue — a deleted row disappears because it
   is really gone).

## Sixth addendum (2026-07-16): twin-rank fidelity + the guard accepts the ELIGIBLE twin set (match picker)

Two follow-ons, one per lineage above — the fourth addendum's fidelity fix reached its FOURTH
surface, and the fifth addendum's twin-merge guard widened for the match picker:

1. **The ledger-check twin matcher had the same cap-flattening tie-arbitrariness disease.**
   A reported bug (2026-07-16): two same-day ledger entries 'ANTHROPIC SAN FRANCISCO USA'
   −14.22 / −14.23 vs same-day statement rows at 14.23 and 14.76 (orig $11.09) — the twin
   matcher paired the MORE-offset combination (14.22 ↔ 14.23; 14.23 shoved onto 14.76). Both
   entries were EXACT-tier claims on the 14.23 row (`amounts_match`'s ±$0.01 absolute floor),
   and the twin rank key `(exact, -shared_tokens, gap)` had NO amount-closeness term — the tie
   fell to entry list order, exactly the disease `_amount_fidelity` fixed at the engine's three
   ranking sites (fourth addendum). `_rank_twin_pairs` (`ledger_diff.py`) now ranks
   `(exact, -shared_tokens, fidelity, gap)`, REUSING `matching._amount_fidelity` — computed on
   the F22 ORIGINAL pair when both sides carry matching-currency originals (the new
   `_orig_pair` helper, which `_orig_match` now also consumes), else the settled figures.
   Ranking-only: no gate/tolerance changed, only which partner wins a tie; 1:1 exclusivity
   unchanged. Pinned fail-first in `tests/test_multi_account_import.py`
   (`test_twin_rank_prefers_the_closest_amount_within_a_tier` — the exact repro, cross-pairing
   reproduced against the unfixed tree; `test_twin_rank_fidelity_reads_the_original_fingerprint_when_both_sides_carry_it`
   — the F22-orig branch beats a settled cent-off near-tie).
2. **The commit guard accepts the ELIGIBLE twin set, not just the assigned winner.** The review
   UI is gaining a match PICKER — the owner wants to swap a weak-band pair row's ledger-side
   candidate ("we actually should be able to choose between one or merge"), and the fifth
   addendum's guard accepted only the 1:1-ASSIGNED twin pairing, so a user-picked alternate
   would have been refused. `_annotate_statement_twins` now RETURNS the pre-assignment
   eligible map `{statement row index → uncovered-entry ids, best-ranked first}` — every
   (entry, row) pairing that passed the twin gates (routed account, ±1-day window,
   exact-or-5%-drift amount, drift-tier shared wording; booked rows and id-less entries
   excluded) BEFORE the 1:1 assignment picked winners — and both check builders stamp it on
   the payload as `twin_eligible`, so the picker's option list rides the preview from the same
   single derivation the guard consumes. `decision_commit`'s guard (`_twin_eligible_ids`,
   replacing `_twin_merge_pairs`) accepts a merge `existing_id` iff it is the engine-proposed
   candidate OR a member of that row's eligible set; anything else still raises
   `ValidationError`. The old acceptance is a strict subset (the assigned twin is always a
   member of its own row's eligible set), and the fail-closed contract is unchanged (an
   underivable check — no window, or an exception — yields no ids, so the merge falls to the
   standard refusal). Pinned in `tests/test_decision_commit_guard.py`:
   `test_picker_merge_into_non_assigned_eligible_twin_is_accepted` (fail-first against the
   unmodified guard; commits with `facts="statement"`, non-picked twin byte-untouched),
   `test_ineligible_live_uncovered_entries_are_still_refused` (wordless drift / outside the
   ±1-day window / past the 5% band — each refused, byte-unchanged),
   `test_fabricated_id_is_refused_even_when_the_row_has_eligible_twins`, and the section-(e)
   originals unmodified-green.

## Seventh addendum (2026-07-16): flat statement-ordered list + filter chips

Owner directive (supersedes the fifth addendum's section-grouped rendering + non-filtering jump-nav, frontend-only): the preview renders ONE flat list in statement order (`rowIndex`) — pair rows embedded inline at their statement position (`tr.imp-pair-tr` colSpan rows inside the literal `imp-table`, per-row side labels replacing the shared colhead), Matched/New section scaffolding deleted; the chips FILTER again (single-select, All default, `.imp-fchip.on`/`--lucius` restored, §8 structural counts partitioning non-dup rows); band bulk bars are contextual (rendered only while their band chip is active, still band-wide); waterfall terms activate chips (empty-target → All) and `jumpToStatementRow` switches to All first when its target is filtered out; the ledger-only block stays a distinct bottom section (All + its own chip), internals unchanged — SI-26/49/53/56/58/60 updated, pinned in `ImportModal.threeState.test.tsx` (flat order, per-chip filtering + order preservation, contextual bulk bars, filtered-out counterpart jump, chip↔counting-model agreement).

## Eighth addendum (2026-07-16): review band defaults to merge + action chips toggle

> **Superseded in part (2026-07-17):** the review band's merge default is now a per-user profile setting — `profile.import_review_default` ("skip" | "merge", normalized in `services/profile.py`, threaded RouterShell → AccountsTab → `ImportModal.importReviewDefault`); the SHIPPED default reverts to **skip** (open-source-safe: a false match must never merge silently for a user who bulk-accepts) while the owner's own profile sets `merge`, keeping his behavior unchanged. Strong stays merge, weak stays new; the toggle semantics below are untouched.

Owner directives (frontend-only, `actionFor`/chip handlers): the review band's default action is now MERGE — defaults ladder strong → merge, review → merge, weak → import-new — because with correct partner assignment and the After column making consequences visible, merge is the durable resolution (reference stamped, re-imports L0-dedupe, labels kept) while skip resolved nothing and re-asked forever; the band keeps its amber question treatment (only the pre-selected action changed) and its bulk bar leads with `Merge all` (first-button-is-the-default). And a live repro (strong row's Skip left no way back to the un-chipped merge default) made all banded rows' action chips TOGGLES: re-tapping the explicitly selected chip clears `matchAction[i]` so the row returns to its band default, chips carry `aria-pressed`, bulk `Skip all` stays available per band — SI-08/SI-08a/SI-28/SI-60 updated, pinned in `ImportModal.threeState.test.tsx` + `ImportModal.match.test.tsx`.

## Ninth addendum (2026-07-16): the match picker UI + the two-question verdict

Frontend halves of two owner features, both consuming data already on the wire (zero backend
change — the sixth addendum's `twin_eligible` + guard widening was built FOR this):

1. **The match picker (SI-63).** A pair row whose statement row index has more than one
   eligible candidate in `ledgerCheck.twin_eligible` (or one differing from the resolved
   partner) grows a quiet "⇄ or match: N other entries ▾" listbox on its LEDGER-NOW column —
   `date · note · amount (same amount | X.X% off)` per candidate, current partner marked. The
   §2 weak-twin join (`ImportModal.weakResolved`, replacing `weakTwinByRowIndex`) becomes a
   three-pass 1:1 assignment: explicit picks (validated against the row's own eligible set —
   the client can never stage a merge the commit guard would refuse) → the server's
   `statement_twin` winners → a STOLEN default falls to its next free eligible candidate
   best-first, so the crossed 14.22/14.23 pair re-pairs in one click. A pick re-points the
   resolved existing through the ONE resolver everywhere at once (ledger column, headline
   evidence — synthesized from figures when the picked entry's `statement_twin` points
   elsewhere, `weakPairEvidence` — after-projection, outcome line, ledger-only population,
   and both commit paths' `existing_id`), and sets the row's action to Merge (an explicit
   partner choice is affirmative intent; the weak band's cautious 'new' default governs only
   the unchosen partner). The picker's Escape closes only the popover (document-level
   listener stops propagation before the Modal's window-level close).
2. **The two-question verdict (SI-64, owner's words: "would all statement transactions be in
   my ledger, and would the amounts match").** The always-open math waterfall is demoted to a
   collapsed "▸ Show the math" expander (jump-terms intact); the PRIMARY verdict is a live
   projected post-commit block: coverage (every row's representative — merge/the matched
   entry, new/its booking, skip-with-match/the matched entry, dups+absorbed covered; a
   no-match skip = "won't be anywhere") and amounts (represented rows' projected figures —
   merge adopts settled unless the kind gate keeps the existing figures
   (`mergeAdoptsStatementFacts`, extracted from `afterImportProjection` so both surfaces
   share one implementation); skip-with-match keeps the existing amount — totaled per
   currency against the statement's own activity for the same rows; uncovered rows excluded
   from both sides so one problem never reports twice). Pure computation
   (`coverageVerdict`/`amountsVerdict` over the component-built `VerdictRowInput[]`, which
   mirrors `confirmImport`'s decision ladder exactly) lives in `importHelpers.ts`; the old
   `imp-insync`/`imp-heads` verdict sentences and their sideLine are superseded (the
   SummaryBar keeps those counts), while the ledger-side residue line + `See below ↓` jump
   survive verbatim as line 3.

Pinned in `ImportModal.matchPicker.test.tsx`, `ImportModal.verdict.test.tsx`,
`importHelpers.verdict.test.ts`; waterfall-visibility pins in
`ImportModal.threeState.test.tsx` adapted to the expander. SI-63/SI-64 added to
`docs/qa/acceptance/statement-import.md`.

## Tenth addendum (2026-07-16): one counting surface per meaning — card row removed, chips band-true

A reported bug + decision (frontend-only): the review step showed THREE counting surfaces with colliding labels — verdict "30 linked · 11 imported new" (outcomes), SummaryBar cards "To import 11 · Will link 30 · …" (outcomes wearing band-like labels), chips "Will link 23 · To import 4 · …" (bands) — "To import 11" vs "To import 4" and "Will link 30" vs "Will link 23" on one screen. Resolution: the SummaryBar card row is REMOVED from the preview step (outcomes are stated ONCE by the verdict lines and echoed only by the footer CTA — both pre-existing and agreeing by construction); the statement-side facts only the cards uniquely carried move onto the context/meta line ("N rows read" always; "N already imported" / "N skipped" only when nonzero, dim `.imp-metafact-dim` — the skipped fact keeps the card's view/hide toggle for the skipped-rows detail as a quiet `.imp-metafact-btn`); the chips get band-true labels that cannot read as outcomes — `Will link` → `Matched`, `To import` → `New` (keys/counts/filtering byte-unchanged; bands stay stable as actions flip); row-level copy untouched ("✓ Will link" / "✓ Already recorded — will link" headlines and the "Will link automatically" band bar are per-row/band outcome statements, correct where they stand). Supersedes the ninth addendum's "the SummaryBar keeps those counts" note and SI-46's 7-card contract. The trust cross-walk between the two vocabularies is pinned in `ImportModal.threeState.test.tsx`: under default actions, chips Matched + Needs you = the verdict's "linked" count and Weak match + New = its "imported new" count (a needs-eyes row default-skips into the coverage WARNING instead of either side). SI-46/56/60 updated; spec owner-amendment note appended.

## Eleventh addendum (2026-07-16): contested clusters assign by min Σ fidelity², not first-pick greedy

A reported bug (2026-07-16): two same-day weak pairs got CROSSED partners — statement 06-15 −14.66 (orig $11.01) / −14.71 (orig $11.05) vs two agent-booked ledger entries −14.18 / −14.11 (one estimated FX rate, no originals). Greedy 1:1 took the single best claim anywhere (14.66↔14.18, diff .48) and forced the order-crossing leftover (14.71↔14.11, .60), when the true, order-preserving pairing — 14.66↔14.11 / 14.71↔14.18, same total |diff| (1.08), minimum total SQUARED diff (0.5834 < 0.5904), monotone (a bigger original settles bigger at any one rate) — was available. Fix, in BOTH assignment homes: `matching._global_claim_assignment` gains an opt-in `fidelity_at` structured-key contract (`key[:fidelity_at]` = decisive leading terms, then fidelity, then date gap, then determinism terms) — claims tied on every leading term (same score band / exactness tier / wording tier) form contested connected components (linked by a shared row or target), and a component of ≥2 claimants × ≥2 targets within `_CONTESTED_CLUSTER_CAP = 8` per side is assigned by exhaustive search: max pairs → min Σ fidelity² → min Σ date-gap → earliest claim order; exact-evidence claims (fidelity ≤ `EXACT_AMOUNT_REL_TOL`, e.g. an F22 fingerprint) peel greedily FIRST and are never re-shuffled by the squared-sum arithmetic; over-cap and single-sided components, lead-separated claims, and the default `fidelity_at=None` keep pure greedy byte-identical. `_assign_batch_matches` passes `fidelity_at=1`; `ledger_diff._annotate_statement_twins` replaces its inline greedy with the SAME shared algorithm (`fidelity_at=2` over its flattened `(exact, -shared, fidelity, gap, e_date, idx)` key); `import_anomalies`' adapter (claim key carries no fidelity term) keeps the greedy default. Pinned fail-first: `tests/test_multi_account_import.py::test_twin_pairs_assign_order_preserving_not_greedy_crossed` (the exact repro, crossed against the unfixed tree) and `tests/dedup/test_match_batch.py::test_contested_cluster_assigns_order_preserving_not_greedy_crossed` (the same shape through `match_batch`, wordless same-day review band); guarded by `::test_exact_fingerprint_pair_is_never_reshuffled_by_the_cluster_optimizer`, `::test_over_cap_cluster_falls_back_to_greedy_1to1` (K9,9 → greedy, 1:1 held), and `::test_over_cap_same_figure_rows_hold_1to1`.

## Twelfth addendum (2026-07-17): three generality fixes from the overfitting audit + the golden-books contract

A generality audit (owner: "are we sure these will be efficient in real life?") re-examined the
constants and tiers this branch tuned against the owner's OWN book for how they behave on books
the engine has never seen — US ACH cadences, merchants that print boilerplate in every
descriptor, three-letter-brand markets, zero-decimal currencies, non-Latin scripts. Three fixes,
each fail-first tested, plus a new named-book regression suite:

1. **Transfer-pair formation gap 2 → 3 days (`TRANSFER_PAIR_MAX_GAP_DAYS`, `services/matching.py`).**
   The 2-day cap (first addendum, fix #1) killed a genuine Friday→Monday transfer — 3 calendar
   days, the single most common weekend shape and typical ACH settlement. Raised to 3; the
   original 7-day Alex Tan false-pair repro stays dead with 4 days of margin
   (`test_formation_seven_day_gap_never_pairs_fail_first`, unchanged). Boundary pins updated:
   a 3-day (Fri→Mon) pair forms, a 4-day pair does not
   (`tests/dedup/test_transfer_pairing.py`).
2. **Shared-reference frequency guard (`REF_TOKEN_MAX_ROWS = 2`, `services/matching.py`).**
   The ≥8-digit shared-token strong tier false-fired on a merchant printing its HOTLINE in
   every descriptor ("CALL 68881234" — passes the date-like and amount screens, shared by two
   DIFFERENT purchases with similar amounts). A reference-token candidate printed on more than
   2 distinct rows of the comparison scope is a merchant constant, not a per-event reference —
   excluded from the tier; the pair still competes through every other tier. `_classify` sees
   one pair at a time, so the scope knowledge is pre-computed once per entry point and threaded
   down (the preview-batch-stamp pattern): `_classify_batch_pairs` builds ONE
   `_reference_token_frequencies` map over rows + ledger pool; `check()` builds it over the
   row + its candidate pool; a bare `_classify` call without scope knowledge (`ref_freq=None`)
   keeps the unguarded behavior. Counted PER TOKEN — three pairs each sharing their OWN
   distinct 9-digit ref (one row per side, 2 rows per token) all still lift
   (`test_distinct_per_pair_references_all_lift_strong_in_one_batch`).
3. **Twin drift-tier token minimum 4 → 3 letters (`ledger_diff._wording_tokens`).** KFC/UOB/
   BHG-class brands — three letters, and in those books the brand IS the wording — could never
   form drift twins under the ≥4 floor. Lowered to ≥3 with a tiny, deliberate
   `_WORDING_STOPWORDS` set (English function words, corporate suffixes, major currency/country
   trigrams) so a shared token is always a merchant signal, never grammar ("the"-only overlap
   forms nothing). The remaining honest gap is now documented in the module docstring AND
   pinned as a choice: tokenization is ASCII-alphabetic, so non-Latin-script books (Vietnamese
   diacritics, Chinese) get exact-tier matching only — the drift tier never fires for them.
4. **Books-close promotion respects the categorized-leg cap (`_promote_books_close`).** Found
   BY the golden-books suite (salary_refund book, fail-first: verdict was
   `('strong', …, promoted=True)` pre-guard): a categorized income + a same-amount expense in
   one complete both-accounts window promoted to strong — batch completeness laundering away
   owner decision #2 ("a categorized leg may ASK, never auto-link, regardless of evidence").
   Promotion now skips a TRANSFER_PAIR carrying a category on either side. NEAR_DUPLICATE
   promotion is untouched (a booked alert entry is legitimately categorized — blocking that
   would break the BUS/MRT flagship case).

**The golden-books contract** (`tests/dedup/test_golden_books.py`): six synthetic end-to-end
books through `match_batch` (and the twin matcher where the book lives there), each named for
the real-world shape it represents — `us_ach_book` (Fri→Mon transfer legs mis-booked as
expense+income → pair forms, review, unconfirmed route), `merchant_hotline_book` (five rows all
printing one phone number → no shared-ref strong lift anywhere), `jpy_zero_decimal_book`
(recurring same-day ¥1,000 charges → wordless pairs stay review, promotion refused as
non-unique, 1:1 holds), `non_latin_book` (exact tiers still form for Chinese/Vietnamese
descriptors; the drift tier's silence is asserted as a CHOICE), `salary_refund_book` (same
amount in/out same week, categorized income → review-capped, never strong, even complete +
known-route), `estimate_drift_book` (the N-pair agent-estimate vs settled cluster →
order-preserving assignment, all weak/non-exact). Each book asserts the FULL verdict set —
bands, assigned partners, promoted flags (twin books: `statement_twin` + the eligible map) — so
a future tolerance tweak that shifts a foreign book's outcome fails loudly with a NAMED book
instead of a bare number drifting in a unit test.

### Agent provenance (twelfth addendum)

- **Decided by:** backend lane agent (Claude), 2026-07-17, executing the owner's overfitting
  audit ("are we sure these will be efficient in real life?").
- **Lane / branch:** `feat/batch-aware-dedup` (backend lane: `matching.py`, `ledger_diff.py`,
  `tests/dedup/**`, `tests/test_multi_account_import.py`).
- **Why this way:** each fix widens exactly one over-tuned dimension (a date cap, a token
  tier, a token floor) while its false-positive repro stays pinned; the golden books turn
  "works on my book" into a named, breakable contract for six book shapes the owner does not
  have.

## Thirteenth addendum (2026-07-17) — the completeness claim is now GATED on the reconcile verdict

`decision_preview`/`decision_commit` no longer claim `BatchContext(complete=True)` on a
printed period alone: the claim additionally requires the statement's own reconcile identity
to PASS (`ok`/`ok_if_flipped`), or the format to be a bank-complete machine export
(OFX/MT940/CAMT) with a declared period and a clean parse. A failed/unknown reconcile →
`complete=False` — bands unchanged, books-close promotion never fires on unproven books.
Commit re-derives the identical claim from the CACHED reconcile verdict. Decided in ADR
`2026-07-17-import-one-pipeline-unification.md` (which also routes every import format
through this engine); pinned by `tests/test_import_pipeline_unification.py::test_gate_*`.

## Fourteenth addendum (2026-07-17) — transfer_leg_echo obeys the formation gap cap (flat 3 days)

Reverses the first addendum's scope line that exempted `transfer_leg_echo` from
`TRANSFER_PAIR_MAX_GAP_DAYS` ("an echo leg legitimately settles days after the transfer's own
booking date"). A reported bug: the owner's sister's round +3,000,000 VND gift (statement row
06-21) echo-flagged against his own unrelated inbound bank transfer for 3,000,000 VND dated
06-28 — a 7-day reach on a round recurring amount (score 0.737, surfaced as "Already booked as
a transfer"). Owner decision: FLAT cap at the same constant for all account types — echo and
formation make the same physical claim ("these two records are one movement"), so both obey
the same settlement window. Considered and deferred, on evidence only: widening card-involved
echoes to 5 calendar days (card payment posting is the one rail that genuinely lags 1–3
business days; instant rails are same-day, but US ACH-style bank rails also lag, so shrinking
non-card windows below 3 was rejected as locale overfitting). Rows with unknown dates keep
matching. Pinned by `tests/dedup/test_transfer_pairing.py::test_echo_seven_day_gap_never_matches`
(fail-first, hermetic same-currency shape — the isolated env has no FX rates, so the live
cross-currency shape would drop at the initial amount gate before echo classification) and
`::test_echo_gap_boundary_inclusive` (3 days still matches).

## Fifteenth addendum (2026-07-17) — word similarity: digit-density filter + containment arm

Owner-driven ("Needs you means users need effort to spend"): AI-written ledger notes vs raw
bank strings systematically failed the symmetric Jaccard bar — the raw side's reference ids
('EW00000000000001', 'FT26173…', hex tails) diluted the union, so true matches fell to the
wordless tier's review cap and landed under "Needs you". Two word-layer-only changes:
(1) `desc_tokens` drops digit-DENSE tokens (≥5 digits — references, not words; numeric brands
'7'/'11'/'2041' stay, F09 pins green); references keep FULL power in the shared-reference and
L0 layers — the owner's explicit future-bank-sync concern, honored by routing, not discarding.
(2) `desc_similar` gains a containment arm: ≥3 shared words covering ≥0.6 of the SMALLER set —
the paraphrase-vs-raw shape (Lucius's clean note ⊂ raw string + gloss). Floor ≥3 blocks generic
verb pairs riding containment. Measured on the owner's live Meridian Bank book (45 rows): 24→16
review, 1→9 strong, 8 promoted, 0 demoted, all 8 verified true pairs. Pinned by the
word-similarity block in `tests/dedup/test_engine_hardening.py`.

## Sixteenth addendum (2026-07-29) — F24: a BOOKED THIRD-PARTY ledger row vetoes pair formation

Promotes the *second* fix above (owner decision #2, "a categorized leg may ASK, never
auto-link") from a score cap to a **hard veto**, for the one shape where the evidence is
definitional. Two live repros from the owner's bank statement, both caught by the coverage
harness, neither auto-booked (the cap held) but both of which the engine should not have ASKED
about at all:

1. A genuine wallet cash-out landing in the bank was offered as pairing with a completed
   ride-hailing expense — categorized, payee'd, on another account, within tolerance.
2. A card **loyalty cashback** credit — which has no second leg anywhere in existence — was
   offered as pairing with another such ride payment.

Decision: in `_classify`'s TRANSFER_PAIR formation branch, if the **candidate** (the LEDGER
side) carries BOTH a resolved `payee_id` AND a `category`, the branch is skipped entirely —
no candidate, not a review-capped one (`_booked_third_party`, `services/matching.py`). A
transaction with a merchant identity *and* a classification is a completed purchase from a
third party; it cannot be a leg of the owner moving money between their own accounts. Both
consequences of the old behavior were bad: a real taxi expense absorbed into a transfer
understates spend and misstates net worth, AND the entry drops off "In your ledger, not on this
statement" — the surface where the owner would have noticed.

**Why the conjunction, and only on the candidate.** `payee_id` alone is NOT a third-party
signal in this codebase and the veto must never be widened to it: `ledger.create_transaction`
MINTS a payee from the raw bank descriptor for every expense/income row, so an untouched
imported leg carries one — measured on a live production book, **907/907** single-leg
expense/income rows have a `payee_id`. `category` alone stays the 2026-07-16 review cap. The
incoming side is exempt by construction as well as by policy: a statement draft carries a payee
NAME (`statements/importer`), never a resolved `payee_id` — resolution happens at the write
choke — so a batch sibling can never be vetoed, and H01 (both legs in one import) is untouched.

**Evidence the veto costs no true positives.** The owner's 25 real `transfer_pair` reviews in
`match_reviews.json`: all 3 the owner actually MERGED had `category=None` on the candidate
(veto would not have fired); 2 of the 3 he DISMISSED had both signals (veto would have spared
him the question). All 7 golden-book transfer-pair scenarios carry `category=None,
payee_id=None` on both sides — `test_golden_books.py` is byte-unchanged.

**Degrade direction verified**, per the 2026-07-29 security review's warning that a tightening
can manufacture duplicates: with no candidate, `combine()` short-circuits to `create` on its
first line regardless of policy or LLM verdict, so the statement row books as its own
transaction and the ledger row stays uncovered. Pinned by
`tests/dedup/test_transfer_pairing.py::test_booked_third_party_candidate_never_forms_a_transfer_pair`
(fail-first), `::test_booked_third_party_veto_beats_a_confirmed_route` (the cashback shape,
stacked against a confirmed route), `::test_booked_third_party_veto_leaves_the_ledger_entry_uncovered`
(the batch door), `::test_raw_opposite_leg_still_forms_a_transfer_pair` (the control), and three
weak-shape pins holding the review cap for category-only, payee-only, and incoming-side signals.
