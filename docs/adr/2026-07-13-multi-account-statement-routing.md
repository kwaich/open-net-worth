# Multi-account statement routing, FX-pair transfers, truncation salvage, per-import cost

**Date**: 2026-07-13 · **Status**: Accepted · **Provenance**: Agent (Claude) build decisions, owner-directed ("shared statement for all my multiplier (sgd, usd, eur) — when import, we can only choose one account. what should be the ideal flow? implement!", "prioritize harness over prompting", "track cost of this properly … and show it")

## Context

A consolidated multi-currency statement (DBS Multiplier, IBKR, Wise…) prints per-currency
sections against ONE account number, while the book models per-currency wallet accounts
sharing a `platform` (custodian model). The import flow targeted a single account — and
`account_balance` folds leg amounts **currency-blind**, so booking a €5,000 row into an SGD
account would add 5,000 to the SGD balance. Routing is a corruption guard, not UX polish.

## Decisions

1. **Rows route by currency, deterministically** (`services/statements/decisions.py`):
   the chosen account anchors its platform group; each row drafts against the sibling wallet
   holding the row's own currency. Chosen wins its own currency; sibling ambiguity resolves by
   the chosen account's last-4 or stays unrouted. All code — no LLM judgment. The one prompt
   change is descriptive (rows report their printed section currency), and the commit guard
   re-checks it: booking a row into an account of a different currency **raises**.
2. **An unroutable currency never default-books.** The row is offered (flag
   `no_account_for_currency`), excluded until the user picks a matching-currency account.
3. **Internal FX conversions pair into ONE transfer** (`_pair_fx_transfers`): the same ≥10-char
   reference token on an OUT row and an IN row in different currencies, both routed, distinct
   accounts, ≤2 days apart → `kind=transfer` with native amounts on each leg (−€1,300 →
   +S$900.00), never fake expense + fake income. The transfer reuses the OUT row's
   external_id, so re-imports L0-dedup the OUT side and echo-flag the IN side. The user can
   break a pair per-row ("Book separately" on the IN leg).
4. **Routing is remembered** on the statement record (`account_map` {currency: account_id},
   stamped at commit; `account_ids` = every wallet that received rows — the record lists under
   each). The next preview of the same source pre-routes from memory — works without platform
   links, and an explicit past choice beats inference.
5. **Truncation harness** (`tiers.py`, found live: a real Meridian Bank statement returned 0 rows
   because ~25 verbose rows overflowed `max_tokens=4096` and the unterminated JSON parsed to []
   silently — two billed runs, nothing read):
   `salvage_row_objects` brace-walks a cap-truncated reply and keeps complete row objects,
   reporting `truncated`; the text tier **split-retries** a truncated chunk (halve, re-read,
   bounded budget); a statement yielding zero rows from garbled replies **raises**
   `StatementParseError` instead of caching an empty parse ("0 transactions read" can no longer
   happen silently). Vision pages salvage + warn (a page can't be split). Row calls now run at
   8192 output tokens.
6. **Per-import cost attribution** (`core/cost_scope.py`): every provider already funnels usage
   through `ai.cost_tracker.record` (global ledger, by source). A ContextVar collector under
   that choke attributes spend to the operation that triggered it — `decision_preview` wraps
   the parse, stamps `llm_cost_usd`/`llm_calls` on the statement record, and returns it in the
   payload. UI shows "AI read cost ≈ $0.042" on the preview meta line and per file in the
   Statements history ("free parse" on format-memory/cache hits). A cached re-preview keeps the
   original parse's cost and bills nothing.

7. **Output-cap policy** (owner question "why do we have this cap?"): the cap is a required
   API parameter and the COST blast-radius bound (a degenerate generation bills to the cap and
   stops — 8192 ≈ $0.12 worst-case vs ~$1 at 64k), never a correctness boundary. Correctness
   is cap-independent via decision #5; `_ROW_MAX_TOKENS = 8192` just keeps splitting rare, and
   a provider that hard-errors on 8192 (task-model routed to an older/smaller model) gets ONE
   retry at `_ROW_MAX_TOKENS_FALLBACK = 4096` (`_row_complete`, text + vision) before the
   loud "reader unavailable" error. Raising the cap alone was rejected: some statement always
   exceeds any fixed cap — the split-retry harness is the durable fix.

8. **Detect-first account choice** (owner: "user has to select one account first?"): the
   choose-step account is OPTIONAL — no account → the parse runs, `meta.account_last4s`
   anchors routing (`resolved_account_id` in the payload, "✦ Detected <account>" in the UI);
   no match → `needs_account` bounces to the picker with the parse CACHED (retry free). The
   detected anchor is a suggestion the user can override — same picker, same re-preview.
9. **F23 — investment legs never transfer-pair** (owner: "why is it flagged? looks wrong"):
   a `kind=investment` cash leg (QQQ buy $700.00) FX-coincided with a statement deposit
   (S$900.00) and classed as its TRANSFER_PAIR. Investment legs are position-trade money
   movements, structurally never a transfer leg — gated in `matching._classify`
   (NEAR_DUPLICATE untouched: same-account wallet-statement rows legitimately near-dup an
   investment's own cash leg). UI: an absorbed FX-pair IN leg no longer renders its own
   Skip/Merge/Import control (it books nothing of its own) until the pair is broken.
10. **Review filter chips** (owner: "hard to see… maybe some filter chip"): All · To import ·
   Already have · Needs eyes, count-labeled, over the same predicates the rows/decisions use;
   filtering preserves original row indices so per-row state survives switching views.

11. **FX pairs check the ledger for the transfer they would book** (owner: "these
   duplications were not detected"): the anomaly layer matched a pair leg against its SIBLING
   DRAFT (existing_id null → unactionable, counted "to import"), shadowing the REAL
   already-booked transfer (agent/email-booked, different external_id namespace so L0 can't
   see it; near-dup is expense/income-only so transfer-vs-transfer had NO class).
   `_existing_pair_transfer` (decisions.py) deterministically finds an existing
   `kind=transfer` with the same from/to wallets, both leg amounts (0.1% tol), ≤2 days —
   both legs then flag transfer_leg_echo with the real id (default skip; merge links the
   statement's external_id onto it), the null-id draft-vs-draft transfer_pair flag is
   suppressed on paired rows, and the COMMIT default refuses to re-book (explicit create
   overrides). Live: both Meridian Bank FT conversions resolved to their agent-booked transfers.

12. **Statement facts refine the card due warning** (owner: "we have warning but it's
   hardcoded based on estimation — use statements to override that, not to replace
   estimated/user-input dates"): `card_statements.statement_summary` reads the newest
   uploaded statement documenting the card (`_latest_statement_facts`, matched via the
   record's account links). FRESH (documents the last closed cycle, ±3d drift tolerance) →
   the printed `payment_due_date` (past = real overdue, where the estimate rolls forward and
   hides it) and `amount_due` (carries balance/interest the charge fold can't see) override
   the WARNING (`due_source="statement"`, banner links the file); stale → pure estimate,
   unchanged. Config fields are never written. A card with no cycle config but an uploaded
   statement derives its cycle from the printed statement date (previously: no warning at
   all). Live: Meridian Bank Visa Infinite resolves due 2026-07-10 / S$3,600.00 / min S$200 off
   stmt_62082feb0000, and the exact-amount payment suppresses the banner.
   **Addendum 2026-07-16 (Meridian Bank repro, two-part):** (a) when facts drive, the
   payment-detection window anchors to THAT statement's own close (`_find_payment` since =
   printed `statement_date`, else `period_end`), never the config-derived cycle close —
   the June statement (printed 06-05, due 06-30, S$2,800.00) under close_day=5 (derived
   close 07-05) made the full 06-26 payment structurally invisible ("overdue by 16 days"
   on a paid card). (b) Recency ladder (`_facts_drive_summary`): a fresh statement one
   cycle OLDER than the derived close keeps driving only while money is still owed on it
   (real debt outranks recency); once FULLY paid it stops driving — a newer cycle closed
   (07-05, S$2,200.00 due 07-30, statement not yet uploaded), so a true-but-stale
   "paid in full" congrats must not mask the current obligation. The summary falls
   through to the newest cycle's ledger estimate with `statement_missing_for_cycle=True`
   (banner: estimate + "Statement closed <date> — upload it to confirm" nudge; congrats
   is reserved for a paid statement that is still the latest close) and, absent a due-day
   config, estimates the due date via the paid statement's own observed close→due offset
   (06-05→06-30 = +25d ⇒ 07-05→~07-30). The plain config path (no fresh statement) is
   unchanged.

13. **run_async propagates contextvars** (owner: "cost says free, why?"): the FastAPI route
   path parses in a dedicated thread; a raw Thread starts with an EMPTY context, orphaning the
   ContextVar cost collector — billed in the global ledger ($0.027, 3 vision calls), "$0 —
   parsed from memory" in the UI. `contextvars.copy_context().run(...)` in the thread fixes
   every ContextVar seam across this boundary; the mislabeled Meridian Bank record was re-stamped from
   the ledger's own entries. Pinned by a route-shaped test (running loop → thread).
14. **Statement viewer + due-banner honesty tiers** (owner): in-app file viewer modal (PDF via
   force-typed blob URL — never served inline from the app origin); banner tiers: estimate
   (says "Estimated", ~amount, Upload-statement action) / statement-backed (remaining,
   paid-so-far, minimum status) / paid-in-full congratulation; "Remind me" pre-fills Lucius
   via a `pendingText` one-shot in the chatComposer store, drained by the ACTIVE composer
   exactly like pendingMentions (a window-event or mount-seed approach misses the
   already-mounted surface — found live).

15. **Exclusive matches** (owner: "there is this transaction, but somehow we are not
   importing them"): two statement rows (7-Eleven Jun 5 + Jun 12, both S$5.40) near-dup
   matched the SAME booked Jun-12 transaction; both defaulted to Skip → the Jun-5 purchase
   silently never imported. `_enforce_exclusive_matches` (import_anomalies): one existing
   txn covers ONE statement row — best score (then closest date) keeps the flag, losers book
   normally. transfer_leg_echo exempt (both legs of one internal move legitimately echo the
   same booked transfer).
16. **Ledger check — the inverse diff** (owner: "highlight what's in the ledger but not in
   the statement"): `statements/ledger_diff.uncovered_ledger_rows` +
   `GET /api/statements/{id}/ledger-diff` — ledger txns in the statement's window + accounts
   minus batch-created, external-id-linked, and engine-matched rows (adjustments excluded;
   ±1d period slack; statement-date fallback window when no period printed). Surfaced as a
   warn panel in the statement viewer with per-row Remove. Live: caught the agent-booked
   Grab −S$13.71 vs settled −S$14.07 (2.6% apart — past near-dup tolerance) plus two more.
17. **Statements modal rebuilt per ui-designer spec** (owner: repeated design misses):
   size xl (content-driven height), divided row list (identity block + right figure +
   secondary Download button), Badge/Stack/Cluster/Money primitives, ErrorState with retry.
   Freehand styling on these surfaces is retired.

18. **Payee alias-prefix fallback** (owner: "why is 7-eleven unsorted?"): exact-alias-only
   resolution missed the book's own payee when the bank appended a location suffix
   ("7 ELEVEN 138 ROBINSO" alias vs "… ROBINSO Singapore SG" line) → booked Unsorted despite
   a default category. `_find_by_alias_prefix` (payee_registry): alias ≥ 8 chars, match ends
   at a token boundary, unique longest winner only — ambiguity falls through, never guesses.
19. **Statement delete** (owner ask): `DELETE /api/statements/{id}` +
   `StatementStore.delete_statement` — removes record + stored bytes; transactions STAY
   (batch rollback owns undo); sha-dedup forgotten so a re-upload re-parses fresh. Ghost
   Delete action in the Statements modal with a three-consequence confirm.
20. **Ledger-check surfaced at commit** (owner: "i still don't see the warning"): the panel
   lived only in the statement viewer; the import done screen now fetches the diff and shows
   the count + directions.

21. **The closing assurance** (owner: "when users import, they don't need to look back at
   their ledger to double check one by one"): the PREVIEW payload carries the pre-commit
   ledger check (`preview_ledger_check` — same offers, no recompute; core vs ±3d boundary
   classification), rendered as an expandable warn panel with per-row Remove on the review
   screen itself; the done screen renders a verdict — green "in sync" (all statement rows
   accounted for + zero core uncovered + reconcile) or the exact attention list. The
   viewer panel remains the post-hoc surface.
22. **Generic category suggestions** (owner: "7-eleven → lucius should guess groceries" —
   pushing back on bespoke logic): the designed-but-never-wired `category_hint` now rides
   the row-extraction prompts, constrained to the USER'S OWN category labels
   (`_category_hint_block`, ≤60 labels, both text + vision tiers), resolved by enrich,
   ✦-badged, NEVER auto-applied (precedence invariant untouched). The alias-prefix fallback
   (decision 18) stays — it solves IDENTITY (deterministic, your curated payee + default),
   not semantics; the two layers compose.

23. **Ledger-check rows explain themselves** (owner: "what the hell" — the panel said "NOT
   on this statement" for a KestrelEats row visibly ON it): the row was substantively right
   (the statement's own copy had already booked; the listed ai row was the true duplicate)
   but verbally wrong. `_annotate_statement_twins` pairs each uncovered row with its most
   plausible statement row (same currency, ±1d of txn/posted date — posted-date matching is
   what catches the 06-05/06-06 shift — amount exact or ≤5%) and the copy states the class:
   duplicate-of-booked-row (safe to remove) / likely-same-settled-differently / genuinely
   absent. L0 exact-dup preview rows now carry a visible "Already imported" tag.

24. **The verdict is never silence** (owner: "i still don't see that statement and ledger
   are matched"): a clean import previously rendered NO panel — absence of warnings read as
   nothing, not as "matched". The preview now always states the verdict: green
   "Statement ⇄ ledger matched" with statement-side dispositions spelled out (to import /
   matched / previously imported / skipped) + the inverse-check result; the warn variant
   carries the same statement-side line so both directions always read together.
25. **Provenance survives statement deletion** (owner question): the durable
   "statement import" signal is the batch's statement_ref_id (outlives the record) — via
   stays labeled, the timeline falls back to the batch description (filename as text),
   rollback unaffected; only the file link dies with the bytes.

26. **Money-level tie-out** (owner: "how about the total amount from the ledger and in the
   statements?"): `_totals` (ledger_diff) computes per routed account the window's ledger
   activity vs the statement net, decomposed EXACTLY — unbooked statement rows, uncovered
   ledger rows, boundary rows, and residual drift (matched entries kept at their original
   amounts — the Merge action's purpose). Live on the Meridian Bank book the identity closed to the
   cent with drift −0.00. `tie=True` only when unbooked/uncovered are zero and drift < 1¢;
   rendered in every verdict variant ("they tie out exactly" / the named breakdown).

27. **Payee resolution: match-first, mint-last + enrich-time hint batch** (owner: "why is
   7-eleven still unsorted? why can't lucius suggest?"): both enrich and the write choke
   resolved `payee OR note` in one shot — the short clean payee won the `or`, missed the
   aliases learned from long bank lines, and the choke MINTED a defaultless payee → Unsorted
   despite a curated default. Order is now clean-payee(no-create) → raw-note(no-create) →
   mint. And the parse-time category_hint only covered LLM tiers — cached re-previews and
   zero-LLM tabular parses never got suggestions; `suggest_category_hints` now runs ONE
   batched completion at enrich time for still-Unsorted rows, persisted onto the cached
   parse with a `hints_attempted` once-per-file gate (a no-yield attempt never re-bills).
   Live: all 10 Meridian Bank rows categorize via payee/rule alone; the batch is the fallback tier.

28. **Review-step hierarchy per ui-designer spec** (owner: "why is duplicates hidden behind
   an expand? what do users care about at import? how can we show the data right?"): verdict
   first (reconcile + ledger check + math fused, never hidden), then one consolidated
   "Needs your decision" Panel (matched bulk, needs-eyes, unrouted, earlier-than-reconciled —
   the latter two jump the filtered table), stat cards demoted to the SummaryBar primitive
   (local Stat component + .imp-stat*/.imp-counts CSS retired), reference table last. Also:
   duplicates panel default-open; category provenance tags removed (tooltip only) per owner;
   statements-modal type calmed; bulk-bar totals show cents; statement issue date displayed.

29. **Transfer base-stamp veto lifted + exact-only heal** (owner: "what the fuck is a legacy
   row with no base_amount?" — the honest answer was NOT legacy): the service stamps
   transfers (`_transfer_base_value`, write-time value for the planning FX reader) but
   `transaction_store`'s normalizer force-nulled the stamp on every transfer — 480/480
   unstamped live, including rows created the same day. Veto lifted (store persists all
   kinds); boot heal `resync_transfer_base_stamps` stamps EXACT cases only (a base-currency
   leg, fx=1.0) — 128/480 healed live; cross-currency history is never back-filled at
   today's rate (historical-rate lock). Bulk-bar totals: leg-scoped for transfers, with the
   native fallback currency-guarded (a ₫ leg never masquerades as S$). Waterfall now splits
   charges vs payments-and-credits so a card statement's net is self-explanatory.
   RECORD CHECK (owner asked): stamping transfers is the DECIDED behavior — ADR 2026-07-09
   (historical-rate lock) says verbatim "fixed prospectively by stamping base_amount/fx_rate
   on transfers at write time"; the store veto silently defeated that decision. The heal goes
   one measured step past "prospectively": it stamps HISTORICAL rows only where the value is
   rate-free (a base-currency leg — a materialization, not a re-valuation); cross-currency
   history stays untouched exactly as the ADR accepted. One test pinned the pre-ADR invariant
   and was updated to pin the decided one.

30. **Exclusive matches REASSIGN, never just strip** (live BUS/MRT incident, 2026-07-16).
    Three same-fare rides in one statement all ranked the SAME booked ride alert as their
    single best match (identical amount + wording; nearest-date ties), so decision #12's
    exclusivity rule kept one claimant and stripped the rest to plain unflagged creates —
    while their true twins sat unclaimed one day away. `_enforce_exclusive_matches` now
    assigns claims GLOBALLY: every row carries its ranked same-class engine candidates
    (`_alts`, popped before the payload), all claims compete best-first (score → date gap
    → file order → alt rank, a total deterministic order), each row and each existing txn
    is assigned at most once, and a loser falls to its next FREE candidate — a plain create
    only when everything is taken. N identical rides now pair 1:1 with N booked alerts.
    The 7-Eleven case (no free alternative) is unchanged.

31. **SUPERSEDED (2026-07-16) — see `.claude/rfcs/batch-aware-dedup.md` and
    `docs/adr/2026-07-16-batch-aware-dedup-books-close.md`.** ~~The ledger-check twin feeds
    BACK onto its statement row~~ (`ledger_twin` flag; owner: "the panel says these are the
    same purchase — why isn't the row marked?"). The residual pair the engine deliberately
    refuses (wording dissimilar + 1 day apart — F21's same-day guard exists because
    same-amount-nearby-days IS what two bus rides look like) was told only in the bottom panel
    while the table row sat unflagged — two surfaces, half a story each. `decision_preview`
    used to attach a `ledger_twin` match flag to the statement row an uncovered ledger row
    EXACTLY twinned (same account+currency, exact amount, ±1 day, twin unbooked, row unclaimed
    by the engine) — same chips, same skip-by-default as any match — with the panel entry
    cross-referencing it ("flagged in the table above", `row_flagged`). This was a THIRD,
    parallel matcher outside the engine (RFC batch-aware-dedup's problem statement names it
    explicitly) — replaced by two engine-level changes instead: (1) the pair-level engine's own
    F21 wordless-gap tier was WIDENED to accept a ≤1-day gap directly (`WORDLESS_MAX_GAP_DAYS`,
    still review-capped pair-level), so the common 1-day-alert-lag case the twin annotator used
    to catch is now a real engine match with a real band, not a fallback flag; (2)
    `matching.match_batch`'s books-close promotion lifts a unique exact-amount pairing inside a
    *complete* statement window straight to `strong` — a batch-level argument the twin
    annotator, scoped to one row at a time, could never make. `ledger_twin`, `_attach_twin_flags`,
    and its FE type/label/CSS are DELETED end-to-end; the ledger-check panel's `statement_twin`
    object (a DIFFERENT, surviving concept — the ≤5%-drift explanatory annotation for genuine
    leftovers) now derives `row_flagged` from whether the paired row carries ANY real engine
    match flag, and the panel's Remove action follows a 4-case table (booked twin / unflagged
    create → Remove is safe; flagged/uncertain twin / no twin at all → never Remove) instead of
    rendering Remove unconditionally for every leftover.

## Evidence

`tests/test_multi_account_import.py` (8), `tests/test_cost_scope.py` (2),
`tests/test_statements_parser.py` (+3 truncation tests). Live: `Statement 6.pdf` — 33 rows,
reconcile ok, 21→SGD wallet / 12→EUR wallet, both FT pairs detected, $0.042 attributed.
