# Statement Import — Acceptance Criteria (RFC statement-import-unified, P1–P3)

The QA contract for the unified statement-import flow. IDs are stable for `make qa-e2e`.

## Faithful Decide-step port

### SI-69
- GIVEN a mixed statement preview containing matched, review, weak, new, and excluded rows
- WHEN the Decide step opens
- THEN every row shows its decision, category/transfer identity, amount, and exclusion state
  without hover, and every matched row also shows a recessed Ledger line before expansion.

### SI-70
- GIVEN a matched row in the Decide step
- WHEN I activate its row disclosure with mouse or keyboard
- THEN the production comparison, override controls, Change match/Back to Lucius's pick, and
  After import projection appear without losing the compact statement or ledger facts.

### SI-71
- GIVEN a needs-eyes or Lucius-excluded row
- WHEN I switch between Leave out and Import/Include
- THEN the dedicated decision chip and visible reason update, and the row remains in statement order.

### SI-72
- GIVEN the import succeeds with newly-created rows
- WHEN I click Mark all N as reviewed
- THEN the button enters a pending state and resolves to a visible reviewed confirmation, while a
  failed request leaves an actionable error in the completion screen.

### SI-73
- GIVEN the Decide step at 1440px, 1024px, or 390px in light or dark theme
- WHEN the modal renders with Lucius open or closed
- THEN no decision/category/match/reason is clipped or hover-only, there is no horizontal page
  overflow, focus remains visible, and mobile rows stack statement → decision/category → amount.

### SI-74
- GIVEN review, weak, or needs-eyes rows
- WHEN I select Needs you
- THEN the rows render as numbered, fully-expanded decision cards with their evidence, truthful
  choice descriptions, and After import result; making a choice keeps the card in place, shows a
  receipt, and Undo restores the undecided state.
- AND when focus is outside an input, `1`, `2`, or `3` activates the corresponding choice on the
  first undecided card.

### SI-75
- GIVEN any matched statement row
- WHEN I expand it
- THEN the detail order is On the statement → equality/drift → In your ledger, followed by one
  radiogroup using Link/Link & update, Keep both, and Leave out, followed by one full-width
  After import result.
- AND the rejected three-peer-column Statement / Your ledger now / After import card is absent.

### SI-76
- GIVEN a duplicate, Lucius skip, needs-eyes exclusion, pre-reconcile exclusion, or explicit user
  skip
- WHEN it appears in All or Left out
- THEN the Left out state, reason, category absence, and struck/dimmed amount are visible without
  hover; any permitted Link to existing / Import override uses the same bordered action language,
  and an override retains full detail plus Undo.

### SI-77
- GIVEN a ledger entry not on the statement or near its boundary
- WHEN the review opens
- THEN it appears in the handoff ledger section as a compact disclosure row; opening it preserves
  its real inspect, link, delete/show-match, explanation, counterpart, and boundary behavior.

### SI-78
- GIVEN the modal is on any import step
- WHEN it renders
- THEN the live production shell carries the handoff's Choose / Read / Decide hierarchy.
- AND the review footer states the live Link / import / leave-out outcome and that nothing writes
  before confirmation, while the successful screen uses the Booked hierarchy and preserves real
  commit errors and reconciliation results.

### SI-79
- GIVEN a successful commit created rows in an import batch
- WHEN I activate Mark all N as reviewed
- THEN the existing batch endpoint runs once, pending and failure states are visible and retryable,
  and success becomes a visible reviewed confirmation without affecting merged rows.

### SI-80
- GIVEN keyboard or screen-reader navigation
- WHEN I operate upload, filters, section headers, disclosures, decision groups, match/category/
  amount edits, footer, and completion actions
- THEN each interactive target is a semantic control with an accessible name, visible focus, and
  correct expanded/selected/checked state; no visible content depends on CSS-generated text.

### SI-81
- GIVEN the Decide step with undecided rows, a drifted linked row, and a ledger-only entry
- WHEN the verdict renders
- THEN it is ONE recessed panel whose every line is a round status badge + a bold headline + a dim
  breakdown (the handoff's `imp3-verdict` / `imp3-vline` / `imp3-vic`), and the amounts line's warn
  form is a BUTTON that filters the list to exactly the drifted rows.
  - ~~"and the tie-out waterfall stays folded behind a quiet 'Show the math' toggle inside the
    same panel"~~ **SUPERSEDED 2026-07-31** — the waterfall is gone entirely; see SI-64.
- AND the statement facts above it read: account (own colour dot) · printed period · ✓ reconciled,
  with the AI read cost pushed to the far right.

### SI-82
- GIVEN any filter is active
- WHEN the rows list renders
- THEN the section header is a count BADGE, then the title, then a right-aligned sub-line, then the
  chevron; the badge takes the tone of what it is showing; and the title NAMES the active band
  ("Already in your ledger — will link" / "New — will import" / "Left out"), falling back to
  "The statement — top to bottom" under All.
- AND the filter chips are rounded pills whose count sits in its own badge, with a warn dot on
  Needs you while anything is undecided.

### SI-83
- GIVEN the Needs-you queue
- WHEN it renders
- THEN it is headed by "Needs you · <x> of <N> decided · press 1 2 3" (no collapsible section —
  a card must never move under the user), each card carries a numeric key badge per choice, the
  chosen choice highlights and the rest dim once decided, and the choice wording is the handoff's
  own ("Same one — update to X" / "Different — keep both" / "Include X" / "Leave out").

### SI-84
- GIVEN any row with a ledger match, an unmatched row, a left-out row, or a ledger-only entry
- WHEN I reach for "which entry does this mean"
- THEN there is ONE picker (`⇄ Change match` / `⇄ Link to existing` / `⇄ Link to a new row`) in one
  bordered-pill CTA family on the row's single action line — never a bare underlined text link and
  never two different popover shapes.
- AND a weak-band twin option stays LIVE (picking one held by another row steals or swaps it) while
  a genuinely consumed manual-link candidate is dimmed and unpickable.

### SI-85
- GIVEN I override one of Lucius's calls on a row
- WHEN the override lands
- THEN the design's teach panel offers to remember why for this source, saving through the real
  instructions endpoint, and the Booked screen says the instruction was saved.

### SI-86
- GIVEN the Decide step footer
- WHEN some rows are still undecided and then all are decided
- THEN the note reads the live outcome plus "nothing is written until you confirm" (naming how many
  undecided rows book the shown defaults), and the commit CTA reads
  "Confirm & book · link N · import M" — turning green only once every row is decided, and reading
  "Nothing to book" when neither a link nor an import would happen.

### SI-100 — the Booked screen names rows the ledger REFUSED as duplicates
The write choke refuses any create that strongly matches something already booked (ADR
`2026-08-01-refuse-strong-duplicates-at-the-write-choke`). A refusal changes what the user GETS:
they asked to import N rows and received fewer. Silence here would turn the feature that ended
silent double-booking into one that introduces a silent SHORTfall — the same contract as a
refused delete (SI-80), so the same treatment: reported, never dropped.

- GIVEN a commit whose response carries `refused_duplicates`
- WHEN the Booked screen renders
- THEN it states how many rows were not imported and why ("already in your ledger"), and NAMES
  each one by date + description — a bare count is not something the user can act on.
- AND a refusal carrying no description still renders as "(unnamed row)" rather than an empty
  fragment that reads as a rendering bug.
- AND when `refused_duplicates` is empty the block does not appear at all.

**Verified at the browser level** — `node frontend/qa/import-refused-duplicates.mjs` self-boots a
disposable isolated instance (never :8080, never live data), navigates Accounts → Import, uploads
a real PDF, picks an account and commits, with ONLY `/api/statements/preview` and `/api/statements/commit`
stubbed at the network boundary. Everything else is the shipped app: the built bundle, the real
`AccountsTab` mapper, the real CSS. 13 checks — the banner reaches the DOM through the real mapper,
names both rows with their dates, paints a non-zero box inside the viewport, is not painted over,
adds no horizontal overflow, and logs no console errors. The parse is stubbed because reaching this
screen otherwise costs an LLM call per run, and the parse is not what this criterion tests.

### SI-87 — the Choose step's dropzone (handoff `DropStep`, unpicked)
- GIVEN the Import modal has just opened
- WHEN I look at it
- THEN it shows ONE centred dashed dropzone reading "Drop a statement here, or browse" over the
  single line "PDF, CSV, XLSX, OFX, QIF, MT940, CAMT — scanned PDFs work too", with
  "or paste a URL / Google Sheet link" as its own quiet control BELOW it — no source tab strip and
  no row of format pills — and clicking that link reveals the URL field, which Cancel puts away.

### SI-88 — the Choose step's picked state (handoff `DropStep`, picked)
- GIVEN I have picked a statement file
- WHEN the Choose step renders
- THEN it shows a file row (format badge · file name · file size · "Change", which returns to the
  dropzone), the "Into accounts" and "Tell Lucius" sections under uppercase labels with their own
  sub-hints, and a CTA row INSIDE the pane reading "You review everything before it books" beside
  the violet "✦ Read the statement" button — and the modal shows no footer on this step at all.

### SI-89 — "I know this source"
- GIVEN the source-peek probe recognises the picked file's fingerprint
- WHEN the Choose step renders
- THEN a violet-tinted banner reads "I know this source. Layout is remembered." with a
  "Re-read from scratch" checkbox that makes the read ignore BOTH the cached parse and the saved
  format profile; an UNRECOGNISED source shows neither the banner nor that checkbox.
- AND WHEN I edit a recognised source's remembered instructions without checking that box
- THEN a hint reads "Instructions changed — Lucius processes the file again with these."

### SI-90 — "Into accounts" chips
- GIVEN the Choose step with no account chosen
- THEN NO chip stands in for the account — the add-chip reads "Select account" and the CTA is
  DISABLED until a real account is picked (2026-07-27: reverted the prior auto-detect default,
  see SI-91 and `docs/adr/2026-07-27-statement-import-drop-account-auto-detect.md`).
- WHEN I pick accounts from the popover
- THEN each becomes a removable chip with its own colour dot, the add-chip becomes "+ Add account",
  and the FIRST chip is the account_id the preview request carries, and the CTA enables.
- WHEN I remove the first chip
- THEN the next one is promoted, and removing the last leaves no chip — the CTA disables again.

### SI-91 — SUPERSEDED 2026-07-27 (owner directive: always require an explicit account)
Previously: the server could answer `needs_account` for a file previewed with no account chosen
(the auto-detect chip's request), and the Choose step withdrew the chip and disabled the CTA.
That path no longer exists in the UI — the CTA is disabled until an account is chosen in the first
place (SI-90), so `needs_account` is no longer reachable from the Choose step. The backend
detection capability itself (`_suggest_account`, SI-25) is unchanged and still tested; only the
frontend's use of it was retired. See
`docs/adr/2026-07-27-statement-import-drop-account-auto-detect.md`.

### SI-92 — the Read step (handoff `ReadStep`)
- GIVEN a statement is being read
- THEN the pane shows a pulsing ✦ orb, "Lucius is reading <file name>", and a phase line that
  advances through "Reading the statement" → "Finding the transaction table" → "Checking the
  statement against its own balances" → "Applying your instructions" → "Checking every row against
  your ledger", holding on the last one for as long as the real read takes.

### SI-93 — auto-categorized ✦ marker on the Decide step (handoff `CatChip`, 2026-07-27 fix)
- GIVEN a draft row whose category Lucius assigned automatically (`autoCat: true`)
- WHEN it renders on the Decide step, NOT as part of a row that will LINK
- THEN its category chip shows a small ✦ (title "Categorised by Lucius") right after the label,
  exactly like the handoff's `CatChip auto` prop.
- WHEN the same row WILL link to an existing ledger entry, or the user manually sets/edits the
  category
- THEN the ✦ does NOT show — a link adopts the matched entry's category, not Lucius's, and a
  manual edit is the user's own call, not Lucius's.

### SI-94 — the LEDGER-ONLY row reads like every other row (owner report, 2026-07-31)
- GIVEN an entry in "In your ledger, not on this statement"
- WHEN its collapsed line renders
- THEN its secondary line never carries the display-projected provenance vocabulary
  (`ai` / `agent` / `import` / `manual`), which is neither actionable nor recognisable to the
  owner.
  - ~~"its secondary line names the ACCOUNT that holds the entry; an unresolvable `account_id`
    OMITS the line rather than printing a raw id"~~ **SUPERSEDED 2026-07-31** by SI-95: the owner
    then asked for "the description + any statement that these transactions already belong to",
    and the STATEMENT the entry is on won that slot — the routed account is the same string on
    every row of a single-account import. The account keeps the criterion, relocated to the
    expanded body's one-line facts run.
- AND its category chip carries the same treatment as the pair and plain rows: an `<i>` swatch in
  the category's own colour, the `none` (muted "Uncategorized") styling ONLY when the entry is
  genuinely uncategorised, and a `kind=transfer` entry showing its transfer identity instead of
  "Uncategorized".
- AND the name ellipsizes with NO native `title` — the browser's own tooltip overlapped the row
  beneath it. The full description stays on the row's `aria-label`; the only surviving native
  tooltip on the row is the static "Original charge" explanation (SI-04,
  `ImportModal.originalCharge.test.tsx`).
- Pinned by `ImportModal.ledgerOnlyRow.test.tsx`.

### SI-95 — "In your ledger, not on this statement": which statement, description, cut-off (2026-07-31)

Owner: *"hide the math here please, let's just show the ledger entries"* · *"show the description
+ any statement that these transactions already belong to"* · *"why the fuck are we showing
already on for what? show description, account, and externalrefs (only if it's a statement) on one
row together!"* · *"why the fuck are we using import batch id? why don't we use external_refs?"* ·
**"please handle these on the backend, not client so the ai tool can get the same information"**.

Supersedes SI-94's secondary-line criterion (see the strike-through above); every other SI-94
criterion still holds and is still pinned by `ImportModal.ledgerOnlyRow.test.tsx`.

- GIVEN an uncovered ledger entry on the Decide step
- THEN its closed row's SECOND line is `on_statements.label` — the statement it is already ON,
  e.g. "5 May – 4 Jun 2026 statement", or "… +1 more" when it is on several. It NEVER reads the
  provenance vocabulary ("ai", "agent") and never a raw `stmt_`/`imp_`/`acc_` id.
- AND **`on_statements` is resolved from `external_refs`, never from `import_batch_id`.** Those
  answer different questions — *what is it on* vs *where did it come from* — and a merge never
  stamps `import_batch_id`, so the batch link cannot see a row a statement LINKED rather than
  created (94 of 310 statement-linked rows in that book). When the entry is on no
  statement we hold, the term is ABSENT: never substituted with the import batch that created it
  (the "Already on: CloudForge USD invoices…" bug), never an empty label.
- AND when the entry sits near a window edge, a second chip beside "Ledger only" carries
  `near_cutoff.label` ("Closing cut-off", "2 days after opening"). No `near_cutoff` → no chip,
  even though `period` and the row's own date are both on the wire.
- AND expanding the row shows exactly ONE line — `description · account · statement · ref <n>` —
  not a stack of labelled rows. Each term drops out when it has nothing to say: the account when
  unresolvable (SI-94's relocated criterion), the statement per the rule above, the bank
  reference when the feed carried none, and the DESCRIPTION when the name rail above already
  showed it in full (repeating a short description there was noise). No term at all → no line.
- **Every fact is computed SERVER-SIDE** (`services/statements/ledger_statement_links.py` and
  `ledger_diff._near_cutoff`) and rides the same `ledger_check` payload the `import_transactions`
  preview returns, so the UI and the AI read one answer. The client derives none of them — not
  the "+N more" count, not the statement-only rule, not the cut-off edge.
- **`near_cutoff` is the boundary bucket, not a second threshold**: non-null IF AND ONLY IF the
  entry is filed under `boundary` (pinned by `tests/test_ledger_diff_statement_links.py`).
- Pinned by `ImportModal.ledgerOnlyStatement.test.tsx`.

## Parse & preview

- **SI-01** GIVEN a text-PDF card statement WHEN previewed via Import → Lucius THEN every
  transaction row appears (count == statement's own row count), with clean payee as the primary
  label and the raw descriptor as the sub-line.
- **SI-02** GIVEN a scanned/photo PDF (no text layer) WHEN previewed THEN rows are extracted via
  vision (no "no selectable text" error) and each row carries a confidence ≤ 0.95.
- **SI-03** GIVEN a CSV/XLSX with no manual mapping WHEN previewed THEN columns are inferred
  (one schema call) and rows parse deterministically; a failed/pending status row (e.g. MoMo
  "Thất bại") is listed under skipped with its reason, never imported.
- **SI-04** GIVEN a row with a foreign pre-conversion charge (e.g. "VND 1,201,200") THEN the
  preview shows the foreign amount under the settled amount, and the booked transaction carries
  original_amount/original_currency.
- **SI-05** GIVEN the statement prints opening/closing balances THEN the preview shows the
  reconcile badge: "✓ Reconciles" when opening + Σ ≈ closing, else "off by $X"; a statement
  without balances shows "not available", never a false ✓.
- **SI-06** GIVEN a low-confidence/guard-flagged row THEN it renders in the preview with a
  "Needs your eyes" chip, EXCLUDED from the import count until the user clicks Include.
- **SI-07** GIVEN the statement's printed last-4 matches an account THEN the preview offers a
  one-click "Import into <account>" suggestion.

## Dedup & decisions

- **SI-08** GIVEN rows already booked (a manual entry / bank sync / earlier import) THEN they are
  flagged "already in your account" with the existing row's summary and a per-row radiogroup:
  Link & update (`merge`), Keep both (`create`), and Leave out (`skip`). The defaults ladder:
  strong → merge, review → the profile's
  `import_review_default` (MERGE by factory default; an explicit `skip` profile setting opts
  out. The default is only a shown recommendation: a review row remains awaiting the user's
  receipt before the final verdict is shown), weak → import-new. The review band KEEPS its
  amber question treatment
  under either default. An explicitly selected override still TOGGLES back to the row's band
  default when re-activated (owner amendment 2026-07-16); the scan verdict and After import
  result provide the feedback, and `aria-checked` always reflects the effective decision.
- **SI-08a** GIVEN a row the engine bands `strong` (score ≥ 0.80, or books-close promoted) THEN
  it renders "✓ Already recorded — will link" (calm, no question mark), DEFAULT action merge,
  with all three handoff decisions visible; re-activating a selected Keep both or Leave out
  override clears it back to the merge default (SI-08's toggle rule — Leave out is never a
  one-way door).
- **SI-09** GIVEN Merge is chosen THEN the existing transaction keeps its category/notes/payee/
  tags/splits, takes the statement's date and settled amount (`facts="statement"` — RFC
  batch-aware-dedup), absorbs the import's external id (re-import → 0 dups), and the row is
  NOT created as new.
- **SI-10** GIVEN the same file is uploaded twice THEN the second preview shows the
  "imported this exact file before" banner and a full re-commit creates 0 transactions.
- **SI-11** GIVEN a statement with more than 25 importable rows WHEN committed THEN ALL rows book
  (the display sample no longer caps the commit).
- **SI-12** GIVEN an import batch (with at least one merge) is rolled back THEN created rows are
  soft-deleted AND merged rows are restored to their exact pre-merge state.

## Enrichment & memory

- **SI-13** GIVEN a merchant with a matching spend rule or payee default THEN the preview shows
  that category BEFORE commit (not "Uncategorized"), and the committed category equals the previewed
  one. An LLM category hint renders with the ✦ badge and is never auto-applied.
- **SI-14** GIVEN a tabular source imported once THEN re-importing the same layout makes ZERO
  LLM calls (format memory), and the preview notes the known format. Deleting the format profile
  (Forget) makes the next import re-infer.
- **SI-15** GIVEN the source's layout changed (same header text, moved columns) THEN the import
  re-learns (never silently mis-parses) and notes "format changed — re-learned".
- **SI-16** GIVEN a user completes the manual column mapping and hits Preview THEN the FILE +
  the MAPPING are sent to the server, the mapping is saved as the source's format profile
  (origin `manual`, keyed by the header-row fingerprint exactly like an inferred profile —
  taught at PREVIEW time, before anything books), and the STANDARD decision preview runs on it
  (zero LLM) — so the manual user gets the byte-identical review (bands, weak twins, ledger
  check, statement record) and the next import of the same layout needs no mapping at all.
  A mapped category column rides along and resolves against the live category store.
  (Superseded behavior: the mapping used to teach only at commit, and the manual preview/commit
  ran a client-side parse — see SI-65.)
  Pinned: tests/test_import_pipeline_unification.py::test_manual_mapping_teaches_then_decision_previews_and_hard_dedups
  + ::test_taught_layout_needs_no_mapping_on_next_import; FE ImportModal.manualRoute.test.tsx.

## Records & history

- **SI-17** GIVEN a committed import THEN the account's Statements section lists the file with
  its period, amount due, reconcile badge, and a working download of the original bytes.

## Chat parity

The chat path is `import_transactions` (`backend/ai/tools/imports.py`), NOT the REST route the
modal uses. SI-18 used to assert parity "at the service layer — the tool and modal call the same
functions", which was true only for the PDF branch: the tool's own routing sent the structured
formats and hand-mapped CSV/XLSX to the legacy `commit_import` wrappers instead (workflow audit
2026-07-18). These criteria therefore verify the TOOL, at the tool's own entry point — a
service-layer test cannot see this class of drift.

- **SI-18** GIVEN the same file dropped into Lucius chat with "import this" THEN the tool preview
  reports the same rows / dedup verdicts / needs-eyes as the modal, and commit via
  {statement_id, decisions} applies identically.
  Pinned: `tests/test_import_tool_pipeline.py::test_preview_commit_roundtrip_with_explicit_decisions`.
- **SI-18a** GIVEN ANY supported format (CSV/XLSX with or without a mapping, OFX, QFX, QIF,
  MT940, CAMT, PDF) previewed through `import_transactions` THEN the response carries the
  decision payload — a `statement_id`, `reconcile`, `ledger_check`, a `needs_eyes` count, and
  per-row `index` / `needs_eyes` / `duplicate` / `flags` — and NO legacy `commit_import` /
  `preview_import` wrapper is reached. A bare `PreviewResult` with no `statement_id` is a FAIL.
  Pinned: `tests/test_import_tool_pipeline.py::test_every_format_previews_through_the_decision_pipeline`
  (the `no_legacy_wrappers` fixture raises if a wrapper is called).
- **SI-18b** GIVEN a chat import of a structured format (OFX/QIF/MT940/CAMT) committed THEN it
  files a statement record AND an `import_batch_id`, and `rollback_import_batch` un-books every
  row it created. A chat import must be exactly as undoable as a UI one.
  Pinned: `tests/test_import_tool_pipeline.py::test_structured_commit_files_a_statement_and_a_rollbackable_batch`.
- **SI-18c** GIVEN a preview returned a `statement_id` WHEN the user says yes and the model
  commits with that same `statement_id` and NO `decisions` THEN the commit reads the CACHED
  parse: zero re-parses, zero re-bills (on a PDF a re-parse is a second vision/LLM charge), and
  the preview's per-row verdicts still apply — needs-eyes/low-confidence rows and exact
  duplicates skip rather than booking blind.
  Pinned: `tests/test_import_tool_pipeline.py::test_commit_with_statement_id_and_no_decisions_reads_the_cached_parse`.
- **SI-18d** GIVEN a one-shot `commit:true` on raw bytes with no prior preview THEN the file is
  parsed EXACTLY once (preview internally, then commit that cache) and a statement record is
  still filed. Two parses for one import is a FAIL.
  Pinned: `tests/test_import_tool_pipeline.py::test_one_shot_commit_parses_exactly_once`.
- **SI-18e** GIVEN the model is told where to import a statement THEN the pipeline entrance is
  actually reachable from the ordinary interactive tool offer/discovery flow: activating
  `statement-import` must not narrow or widen that surface, and the `add_transactions` description
  names `import_transactions` ahead of any skill reference. Guidance that sends the model away from
  the production entrance is a FAIL even when every service-layer test passes.
  Pinned: `tests/test_import_tool_pipeline.py::test_import_transactions_is_in_the_statement_import_skill_allowlist`
  + `::test_import_transactions_is_in_the_core_tool_offer`
  + `::test_statement_guidance_points_at_the_pipeline_not_hand_parsing`.

## Decision clarity & provenance

- **SI-19** GIVEN a preview row matched to an existing transaction (either band) WHEN the user
  selects an action THEN an explicit outcome line (`outcomeLine`, band-agnostic — used by both
  the Will-link and Needs-you rows) states what will happen: Merge with nothing changed reads
  "Marks it settled · keeps your category & notes"; Merge with only the date re-stamped reads
  "06-06 → 06-05 · keeps your category & notes"; Merge with only the amount changed reads
  "S$12.00 → S$11.85 · keeps your category & notes"; Merge with both changed reads
  "06-06 → 06-05 · S$12.00 → S$11.85 · keeps your category & notes"; Merge onto a transfer echo
  reads "Links to your existing transfer · its legs stay as booked"; Merge onto a transfer_pair
  formation (owner decision 2026-07-16 — §2b) reads "Marks it cleared & links the statement's
  reference · date, amount, category & notes stay as they are · this row won't be booked on its
  own" — an HONEST description of what `apply_import_merge` actually does for a kind-mismatched
  pair (the two legs are always opposite kinds by construction, so the merge's kind-match gate
  never fires: no transfer is rebuilt, no date/amount rewritten); Import new reads "Adds a
  second transaction next to the one you already have" (either band); Skip shows no outcome line.
- **SI-20** GIVEN a transaction created (or merged) by a statement import WHEN its drawer is opened
  THEN Provenance shows "Added by You · via Statement import" and a "From <filename>" row that
  downloads the original statement (`/api/statements/{id}/file`); the Activity timeline replaces the
  raw "batch:imp_…" note with the resolved "from <filename>" link. Applies retroactively to batches
  imported before this feature (context is resolved from the row's import_batch_id at read time).

## Multi-account routing & cost (consolidated statements)

- **SI-21** GIVEN a consolidated multi-currency statement (one account number, per-currency
  sections) and per-currency wallet accounts sharing a `platform` WHEN previewed into ANY of
  them THEN each row routes to the wallet holding its own currency (Account column per row +
  a routing banner "SGD → X · EUR → Y"), and dedup flags are computed against each row's
  ROUTED account.
- **SI-22** GIVEN a row in a currency no account holds THEN it is flagged
  "No account in this currency", excluded by default, and commit NEVER books it into a
  mismatched account (an explicit mismatched pick is refused loudly — the balance fold is
  currency-blind).
- **SI-23** GIVEN two rows in different currency sections sharing one bank reference
  (an internal FX conversion) THEN they book as ONE two-leg transfer with native amounts
  (never fake income + expense); the IN leg shows "Folds into the transfer" with a
  "Book separately" override; re-importing the same file re-creates nothing.
- **SI-24** GIVEN any LLM-parsed preview THEN the meta line shows what the parse cost
  ("AI read cost ≈ $0.042" / "$0 — parsed from memory"), the statement record stores it, the
  Statements history lists it per file, and the global AI-cost ledger counts the same calls
  under statement_* sources. A truncated model reply is salvaged + split-retried — a
  statement can never silently read as "0 transactions". GIVEN a card statement printing a
  minimum payment THEN the "Due …" fact grows a `· min $X` fragment right after the amount
  due (same currency formatter); absent or zero → no fragment (was parsed and carried on
  `meta.minimum_payment` all along — a frontend type/render gap, fixed).
- **SI-25** Backend-only capability as of 2026-07-27 (`docs/adr/2026-07-27-statement-import-drop-
  account-auto-detect.md`) — no frontend path calls `POST /api/statements/preview` with an empty
  `account_id` any more (SI-90 requires an explicit account before the Choose step's CTA enables).
  Still real and tested for any other caller that omits it (REST/MCP/a future integration): GIVEN
  `POST /api/statements/preview` called with no `account_id` WHEN the statement prints an account
  number matching one of the user's accounts THEN the preview anchors to it automatically
  (`resolved_account_id` in the response) and routing/dedup behave exactly as an explicit choice;
  WHEN nothing matches THEN the response carries `needs_account: true` instead of guessing — and
  the retry is free (parse cached, zero LLM calls).
  Detect is LAYERED and deterministic-first (2026-07-17, a reported bug `stmt_ab4a65dc0000`: a
  Meridian Bank XLSX's printed account number was invisible because the tabular tiers never run
  the metadata LLM call — `account_last4s` was structurally ALWAYS empty for grids):
  - **Printed digits (layer 1, LABEL-FREE)**: tabular parses deterministically harvest the
    PREAMBLE (rows above the header) for contiguous ASCII digit runs ≥ 6 and carry their
    last-4s on `meta.account_last4s` (set-union with whatever an LLM meta call contributes on
    document tiers; masked card tails like "xxxx-xxxx-1234" included, scoped to cells carrying
    x/X/* mask chars). NO label vocabulary (labels are per-country and never finish) — run
    contiguity excludes dates/amounts syntactically, and the SEMANTIC filter is the
    intersection with the user's OWN accounts' `last_four`. Accepted consequence: any preamble
    number ending in a stored last-four (e.g. a citizen ID) can suggest — the blast radius is
    a suggestion the user sees, never a booking.
  - **Ambiguity → ask**: matched accounts confined to ONE shared platform are that custodian's
    currency wallets (one printed number, many wallets — SI-21) and anchor the first; matches
    spanning DIFFERENT platforms suggest NOTHING (honest beats guessing), and the ambiguous
    printed claim also blocks the memory fallback below.
  - **Profile memory (layer 2)**: no printed match at all → the account this source's format
    profile last COMMITTED into (`account_id`, stamped by `decision_commit` on a booking
    commit; read LIVE from the store, never the cached snapshot) if it still exists
    non-archived. The statement's own claim always beats memory.
  - **Cached-parse self-heal**: a pre-fix tabular record cached with empty `account_last4s`
    re-harvests from the same bytes on its next preview (header position from the learned
    format profile; no profile → no heal) and persists the healed meta — zero LLM, zero
    re-parse.
  - Both layers missing → `needs_account`, never a guess (unchanged).
  Pinned: tests/test_statement_account_detect.py.
- **SI-26** SUPERSEDED twice — the original filter chips were first retired for non-filtering
  jump-nav (pair-reconciliation spec §11), then the owner amendment of 2026-07-16 brought
  FILTERING back as the chips' behavior (see SI-56 for the current contract). The original
  concern is live again and pinned there: per-row Skip/Merge/Import-new state survives
  switching filters (decisions are keyed by draft index, never by visibility).
- **SI-27** GIVEN an existing `kind=investment` cash leg whose amount FX-coincides with a
  statement row THEN it is NEVER offered as a transfer-pair match (F23) — an investment leg
  is a position trade's money movement, not a transfer leg. An FX-pair IN leg shows no match
  control only while a new/existing two-leg transfer safely represents both legs. A
  `near_duplicate` whose `existing.kind` is not `transfer` breaks the parser pair: both
  decisions stay visible and every explicit IN-leg merge/create/skip reaches commit.
  Low-confidence, Lucius-skip, and pre-reconciliation guards run before automatic separate
  creation, so an excluded split IN emits `skip`, never `create`.
- **SI-28** GIVEN an FX pair whose two-leg transfer ALREADY exists in the ledger (same
  wallets, both leg amounts, ≤2 days — however it was booked: agent, email, hand) THEN both
  legs flag "already booked as a transfer" with the REAL transaction id (actionable
  Skip/Merge/Import-new; default per the SI-08 ladder — a merge into a transfer target keeps
  its legs whole, marking it cleared + absorbing the reference), the draft-vs-draft pair noise
  is suppressed, and a default commit books NOTHING NEW for that pair; only an explicit
  Import-new re-books.
- **SI-29** GIVEN a preview where every row is already in the ledger THEN the primary button
  reads "File statement — nothing new" (enabled): committing books ZERO transactions but
  files the record under every routed wallet's Statements history, remembers the routing,
  and arms the "already imported" banner. Upload/link lifecycle: bytes + record stored at
  PREVIEW (sha-deduped); account links (`account_id` anchor + `account_ids` per routed
  wallet) stamped at COMMIT — including an all-skip filing commit.
- **SI-30** GIVEN a credit card with an uploaded statement documenting the last closed cycle
  THEN the due warning uses the PRINTED facts — due date (past dates show real overdue),
  amount due (includes carried balance/interest the charge fold can't see), minimum payment —
  labeled "from your statement" with a download link; the account's own estimated/user-input
  config (close day, due day) is never modified and remains the fallback. A card with NO
  cycle config but an uploaded statement now gets the warning derived from the statement.

## Statement viewer & card due banner

- **SI-31** GIVEN a statement row WHEN its filename is clicked THEN a modal previews the file
  in-app (PDF via the browser viewer on a force-typed blob URL — uploaded bytes can never
  execute as a document; text formats render as text; others offer the download), titled with
  the file's period/due facts and a download action.
- **SI-32** GIVEN a card's due banner THEN it is honest about its source: estimates lead with
  "Estimated", show "~" on the amount, and offer an "Upload statement" action; statement-backed
  banners show remaining, paid-so-far ("Paid X of Y — Z still due"), and minimum-payment status
  ("minimum S$50" / "not yet met" / "minimum met") with the statement link; a FULLY PAID
  statement shows a congratulation instead of a warning; "✦ Remind me" opens the Lucius dock
  with a pre-filled reminder request (pendingText drain — lands in whichever composer surface
  is visible).

## Ledger check & match exclusivity

- **SI-33** GIVEN one existing ledger transaction and TWO statement rows that both match it
  THEN only the better claim (score, then date proximity) keeps the match flag — the other
  row books normally (exclusive matches; the Jun-5/Jun-12 7-Eleven data-loss case). Both legs
  of an internal transfer may still echo the same booked transfer (exempt class).
- **SI-34** GIVEN a filed statement THEN the viewer shows a "ledger check" panel listing
  ledger transactions in the covered period + accounts that the statement does NOT contain
  (batch-created, external-id-linked, and engine-matched rows are excluded) — each with a
  Remove action; the panel is advisory and the viewer works without it. Statement rows the
  ledger lacks are already covered by the import flow itself.
- **SI-35** GIVEN a statement in the Statements modal WHEN Delete is confirmed THEN the record
  AND the stored file are removed (404 on re-delete), transactions it imported STAY in the
  ledger, and re-uploading the same bytes files as a fresh record (sha-dedup forgotten). The
  confirm copy states all three consequences.
- **SI-36** GIVEN a payee whose stored alias is a long (≥8-char) prefix of a statement line at
  a token boundary (bank-appended "Singapore SG" suffixes) THEN the payee resolves — enrich
  shows its default category and the booked row is categorized; short aliases and ambiguous
  ties never prefix-match (the 7-Eleven Uncategorized case).
- **SI-37** GIVEN a decision commit completes THEN the done screen surfaces the ledger-check
  count ("N transactions in your ledger during this period are not on this statement") with
  directions to the statement viewer panel.
- **SI-38** GIVEN an import preview THEN the SAME screen carries the pre-commit ledger check:
  "N transactions in your ledger for this period are NOT on this statement" (expandable rows
  with Remove), with cut-off-adjacent rows classified separately ("likely on the
  previous/next statement" — never silently dropped, never crying wolf); GIVEN a commit
  completes with zero core uncovered rows THEN the done screen shows the green in-sync
  verdict ("every statement row accounted for · nothing missing from the statement ·
  reconciles"). The user never has to audit the ledger row by row after an import.
- **SI-39** GIVEN the user's category book WHEN a statement parses THEN each row may carry a
  ✦ category suggestion chosen from the USER'S OWN labels (generic LLM inference — e.g.
  7-Eleven → Groceries), display-only and never auto-applied; the deterministic chain
  (rule > payee default) still wins where it applies.
- **SI-40** GIVEN a ledger-check row that pairs with a statement row (same currency, ±1 day of
  the txn OR posted date, amount exact or within 5%) THEN the panel names it — "Duplicate of
  'fp*Kestrel Eats' (−S$21.80) on this statement — already booked, safe to remove" / "Likely the
  same purchase as … (settled amount differs)" — and only a row with NO plausible twin reads
  "Not on this statement — worth a closer look". GIVEN an L0 exact-duplicate row in the
  preview table THEN it carries a visible "Already imported" tag explaining it will not
  import again.
- **SI-41** SUPERSEDED (pair-reconciliation spec §7.2) — the ledger-check panel's two named
  groups ("Duplicates to clean up" / "Not on this statement") are retired as a user-facing
  concept. Replaced by **SI-61**.
- **SI-42** GIVEN any preview THEN a match verdict is ALWAYS visible — never communicated by
  silence: clean → a green "✓ Statement ⇄ ledger matched. All N statement rows accounted for
  — X will link · Y to import · Z need your review · W previously imported." (each fragment
  omitted when zero; boundary rows noted); issues → the warn panel, which also
  carries the statement-side counts so both directions read on one screen. A statement with
  no printed period says so instead of implying a check that didn't run.
- **SI-43** GIVEN a deleted statement THEN audit provenance survives: events + the batch are
  immutable, "via Statement import" stays (durable statement_ref_id on the batch), the
  timeline shows the batch description (which carries the filename as text), batch rollback
  still works; only the file link dies.
- **SI-44** GIVEN any preview or filed statement THEN the verdict includes the money-level
  tie-out: "Amounts: ledger −S$X vs statement −S$Y — they tie out exactly" when clean, else
  the difference DECOMPOSED to the cent (not booked yet · flagged above · near the cut-off ·
  matched entries kept at their original amounts, with Merge named as the fix); the identity
  ledger = (statement − unbooked) + uncovered + boundary + drift holds exactly by
  construction (single-account statements; multi-account shows per-account statement nets).
- **SI-45** GIVEN a row whose clean LLM payee misses the book THEN resolution falls back to
  the RAW note before anything mints (clean-payee → note → mint-last): the 7-Eleven case now
  enriches AND books with the payee's default category. GIVEN rows still Uncategorized after the
  deterministic chain THEN ONE batched completion suggests categories from the user's own
  labels (✦, never auto-applied) — at ENRICH time, so cached parses and zero-LLM tabular
  parses get suggestions too; the batch runs at most once per file (hints persist on the
  cached parse; a no-yield attempt never re-bills).
- **SI-46** (owner amendment 2026-07-16 — ONE counting surface per meaning; supersedes this
  criterion's original 7-card SummaryBar row and its section-grouped skeleton, whose
  flat-list half SI-56 had already superseded) GIVEN the review step THEN it renders inside a
  workspace-size modal as: heads-up strips → the context/meta facts line → the VERDICT zone
  (never hidden) → an earlier-than-reconciled banner (only when nonzero) → filter chips
  (SI-56) → the flat statement-ordered list (SI-56) — NO card row anywhere, and no two
  counting surfaces share a label across meanings:
  - OUTCOMES (what commit will do, live) are stated ONCE by the verdict lines ("N linked to
    entries you already had · M imported new" + warning variants, SI-64) and echoed only by
    the footer CTA ("Link N · import M") — the two agree by construction.
  - STATEMENT-SIDE facts ride the meta line: "R rows read" always; "D already imported" and
    "K skipped" only when nonzero (dim, quiet); the skipped fact is the view/hide toggle for
    the skipped-rows detail the card used to open.
  - FILTER OWNERSHIP (the one place each row can be found) is counted ONCE, by the handoff
    chips (`All`, `Will link`, `Needs you`, `New`, `Left out`, `Not on statement`). Weak
    possible matches live only inside `Needs you`; there is no overlapping Weak filter or
    separate weak bulk bar. Review, weak, and read-check cards remain in `Needs you` after a
    decision so their receipt and Undo stay visible. Other rows move with their effective
    outcome (for example, an explicit strong-row Leave out moves from `Will link` to `Left
    out`). The filter populations partition All, pinned in `ImportModal.threeState.test.tsx`;
    live link/import outcomes remain independently pinned to the verdict and footer.
- **SI-47** GIVEN N statement rows of the same amount and wording whose ledger twins were
  booked from another source (e.g. card-alert emails, each dated a day later) WHEN they all
  rank the same existing transaction as their best match THEN losing claims REASSIGN to
  their next free twin (N rows pair 1:1 with N ledger entries — each flagged with a
  DISTINCT existing transaction); a claim only falls back to a plain create when every
  candidate is taken (the 7-Eleven single-twin case). The global 1:1 assignment is now
  `matching.match_batch`'s own job (RFC batch-aware-dedup) — this user-visible behavior is
  unchanged.
- **SI-48** SUPERSEDED — the `ledger_twin` flag (a separate, posted-date-aware fallback
  matcher bolted onto the table row) is DELETED (RFC batch-aware-dedup, ADR
  `2026-07-13-multi-account-statement-routing.md` #31 marked superseded). Replaced by:
  - **SI-48a** GIVEN N statement rows the pair-level engine alone couldn't confidently match
    (wording dissimilar, ≤1 day apart) but which are the unique 1:1 exact-amount pairing
    inside a complete statement window THEN books-close promotion lifts them to the strong
    band — each renders "✓ Already recorded — will link" instead of a skip-defaulted
    question.
  - **SI-48b** SUPERSEDED (pair-reconciliation spec §6.5) — the 4-case (A/B/C/D) Remove/jump
    rule is retired as a user-facing taxonomy. Replaced by **SI-57**.
- **SI-49** GIVEN a fully-reconciled statement (every row strong-matched or already imported)
  THEN the flat list is calm strong-band pair rows (plus any already-imported dup rows) in
  statement order, no review/weak treatment and no LEDGER-ONLY block renders, no bulk bar
  renders (contextual, chip-gated — SI-60), the primary button reads "Link N — nothing new",
  and zero rows show a Skip/Merge/Import-new question.
- **SI-50** GIVEN a matched row (either band — needs-you OR will-link; will-link previously
  named the existing transaction NOWHERE) THEN one quiet dim sub-line renders under the
  headline with the existing transaction's own date (MM-DD, matching the table) · account
  NAME · category LABEL ("Uncategorized" when blank, the raw stored value when present-but-
  unresolvable — never "undefined") · a source phrase (`ai`→"via Lucius", `agent`→"via
  agent", `import`→"from an earlier import", else/empty→"added by you") · its signed native
  amount — e.g. `— 06-09 · UOB Visa Infinite · Transport · via agent · -S$11.00`. (Location
  updated by SI-54's pair anatomy: the quoted note + this identity line render in the pair
  row's YOUR LEDGER NOW column, every band.) AND clicking its name (the ledger column's
  quoted note) fetches (`GET /api/transactions/{id}` — the row may not be on the
  currently-loaded register page) and opens the REAL transaction drawer (TxnDetail, the same
  component/endpoints the register uses — an edit is allowed) ABOVE the import modal; a failed
  fetch shows inline "Couldn't open — try again." instead of failing silently; closing the
  drawer (Esc/backdrop/X) returns to the import modal WITHOUT remounting it — every row's
  selected Skip/Merge/Import-new action survives untouched (the import preview is deliberately
  not refreshed after a drawer edit — the commit-time guard re-derives every match server-side
  against the live ledger, so a stale preview flag can never mis-merge).
  AND on a will-link (merge) row, the outcome line enumerates the FULL delta the link will
  write onto the existing transaction, not just date/amount: a date re-stamp
  ("06-06 → 06-05"), an amount change ("S$10.00 → S$11.00"), and — when the draft carries a
  pre-conversion original charge the existing row LACKS — "adds the original charge ($11.09)"
  (the merge's own foreign-charge gap-fill, `apply_import_merge`: copied in only when missing,
  never overwrites one the existing row already has, so the fragment never appears in that
  case); no delta at all falls back to "Marks it settled"; "keeps your category & notes" is
  always the closing fragment. (The merge separately absorbs the statement's own reference
  onto the existing row's `external_ids` for future dedup — an existing, tested
  `apply_import_merge` behavior, not a new visible fragment: there is nothing on the existing
  row's reference for the user to compare against pre-merge, so it is not user-observable the
  way date/amount/original-charge are.)
- **SI-51** GIVEN the ledger-check panel's boundary summary line ("N entries sit near the
  cut-off — likely the previous/next statement") THEN it is collapsed by default (a quiet
  month stays quiet) with its own chevron affordance, independent of the main panel's
  `checkOpen` toggle — present in BOTH the fully-matched verdict (no core leftovers, boundary
  only) and the issues verdict (dups/absent present too). WHEN expanded THEN the boundary rows
  render under TWO sub-headers keyed by the SERVER's `near_cutoff.edge` (SI-95) — "Near the
  opening cut-off — likely the previous statement" then "Near the closing cut-off — likely the
  next statement" — each row carrying the SAME full row treatment as a core entry (date · note ·
  an account/category/statement/cut-off detail line, each term absent when the server has no
  answer for it · signed amount). (Superseded 2026-07-31: the
  sub-headers used to be computed CLIENT-SIDE as `date > period.end` vs everything else, which
  was dead code — `period` on the wire is the already-widened window and no emitted row can fall
  outside it, so the "dated after the close" group never rendered once.) NO
  twin, NO verdict, and NO Remove action renders on a boundary row — pure visibility (the
  backend already excludes any row within `_BOUNDARY_DAYS` of the window edge from twin
  annotation). GIVEN an empty boundary list THEN nothing renders (no dead toggle).
- **SI-52** GIVEN ANY ledger-check panel entry — core cases A–D, or an expanded boundary row
  (SI-51) — THEN its name renders as the SAME link-styled button (`.imp-txn-link`) the match
  table's own rows use, and clicking it opens the SAME TxnDetail drawer plumbing
  (`GET /api/transactions/{id}`, `.imp-drawer-lift` — no duplicate fetch/drawer logic; the
  panel row's `id` IS the ledger transaction's own id, never an external_id); a failed fetch
  shows inline "Couldn't open — try again." instead of failing silently — this can legitimately
  happen for a STALE panel row (the ledger-check snapshot isn't re-fetched live; a row can be
  merged/deleted elsewhere — e.g. a dedup-review merge tombstones the absorbed row — between
  the preview and the click) and is the correct, by-design failure mode, not a bug: retrying
  won't resurrect a genuinely gone row, but the panel never crashes or silently no-ops either.
  GIVEN case D (a true orphan, no plausible twin anywhere) THEN its copy is position-aware: any
  row within `_BOUNDARY_DAYS` of either window edge already sorted into the boundary bucket
  (SI-51) instead of reaching case D, so a case-D row is ALWAYS mid-window — it reads "A real
  card charge should appear on its own statement — if it's not on the next one either, it's
  likely a double-booking. Open it to inspect." The 4-case action table (owner amendment
  2026-07-16, live-testing the shipped build): **A** (booked twin) → Remove; **B** (unflagged-
  create twin) → Remove; **C** (flagged/uncertain twin) → "Show row ↑" only; **D** (true
  orphan) → the drawer link (inspect first) PLUS a "Delete" button (same danger/confirm/busy/
  failure DELETE choke as Remove, `removeCheckRow` — labeled "Delete" rather than "Remove" to
  read as a deliberate action on an unrelated row rather than "clean up this known dupe";
  batch-cleaning several phantom agent-booked rows one drawer-open at a time was too slow).
  Expanded boundary rows (SI-51) are UNCHANGED by this amendment — still pure visibility, no
  action button of any kind ("surface, don't conclude" holds there, not on a real orphan).
- **SI-53** — **RETIRED 2026-07-29** (RFC `one-match-engine-instructions-as-policy` §D3,
  acceptance `docs/qa/acceptance/statement-import-unified-match.md` · SIUM-NO-DEADEND). The counterpart line below was derived entirely from
  `ledgerCheck.rows[].statement_twin`, which no longer exists: `preview_ledger_check` is a pure
  set difference now, so a claimed entry is simply absent from the panel and there is no
  counterpart to name. `counterpartLine`, its `jumpToStatementRow` call sites and the
  `Show match ↑` button are all deleted; the criterion is kept below for history only.
  <br>(original text) (owner amendment 2026-07-16, live-testing feedback — items 1–2 of 3; item 3 is
  now DONE: the pair-reconciliation table (SI-55) moved weak/drift twin matches into the
  table as the weak band, eliminating case B from the ledger-check surface entirely)
  GIVEN any twin-bearing panel entry (cases A/B/C — anything with `statement_twin`) THEN a
  second quiet line renders under the existing explanation, naming the counterpart explicitly
  instead of leaving the user to search "that row / the table above" for it: `Statement row:
  MM-DD · "‹description›" · ‹signed amount›` followed by `— same amount` when
  `statement_twin.exact` is true, or `— settled ‹drift›% off your ‹ledger amount›` when false
  (drift computed client-side from the twin's `amount` vs this row's own `amount`, e.g. a
  ledger −14.16 vs a statement −14.63 reads "settled 3.3% off your 14.16"). Case D (no twin)
  renders no such line. **Update (owner feedback, live testing 2026-07-16): the counterpart
  line itself is a `<button>`** (`.imp-txn-link.imp-txn-link-dim` — the `.imp-txn-link`
  button-chrome reset, styling stays visually dim rather than accent-colored, brightening only
  on hover/focus) on EVERY twin-bearing entry — cases A, B, and C alike, not just C. Clicking it
  calls the SAME `jumpToStatementRow` helper as case C's dedicated "Show row ↑" (below): switch-
  filter-first when the target is hidden under the current chip, then scroll + `.imp-row-flash`
  the exact row; a twin with no resolvable `row_index` falls back to the same filter-only jump,
  never a dead click. Case C keeps its separate "Show row ↑" button too — redundancy is fine, by
  design. `aria-label="Show statement row ‹MM-DD›"` (omits the date suffix, never the literal
  string "undefined", when the twin carries none). GIVEN "Show match ↑" (or the counterpart
  line) THEN it is a TARGETED jump: it resolves the twin's own `row_index` to the matching
  draft (via `ImportPreviewDraft.rowIndex`), switches the filter back to All FIRST when the
  active chip hides the target (owner directive 2026-07-16 — the chips filter again, SI-56),
  scrolls that EXACT row into view and flash-highlights it (`.imp-row-flash`, a ~2s one-shot
  fade, not a repeating pulse — respects `prefers-reduced-motion` via the existing global
  rule, no separate media query needed). A twin with no resolvable `row_index` falls back to
  All + scrolling to the top of the reconciliation table — never a dead click.

## Pair-centric reconciliation behavior (legacy IDs; visual anatomy superseded by SI-69–SI-80)

- **SI-54** SUPERSEDED visually by SI-69, SI-70, and SI-75. GIVEN a matched row THEN its
  compact statement row shows the current decision, category/transfer identity, amount, and a
  recessed matched-ledger line before expansion. WHEN expanded THEN it renders On the statement
  ↔ In your ledger, one three-choice radiogroup, then one full-width After import result. The
  rejected three-peer Statement / Your ledger now / After import body must not render.
- **SI-55** — **SUPERSEDED 2026-07-29 by `SIUM-WEAK-*`** (`docs/qa/acceptance/statement-import-unified-match.md`). The weak band is no
  longer derived client-side from a ledger-check twin: it is the ENGINE's own settled-drift
  tier, arriving as `flags[].band === 'weak'` on an ordinary match flag with 1:1 exclusivity
  already resolved. It is also no longer a `Needs you` row — it has a safe default (Import).
  (2026-07-31: this line also named a one-click `Not the same` dismissal; that control is
  removed, and `Import` now carries its release of the matched ledger entry — ADR
  `2026-07-31-weak-import-releases-the-entry`.) Original text below, for history.
  <br>(original text) GIVEN a statement row the main engine did not confidently match, but whose
  `ledgerCheck.rows[].statement_twin` resolves to it AND that ledger row is not already
  claimed by a strong/review match (nor an L0 dup, an FX-pair leg, or a needs-eyes row) THEN
  it renders as a `weak` band pair row with headline "≈ Possibly already recorded —
  {evidence}" (evidence = "same amount, N days apart" or "settled X.X% off"), default action
  Keep both, and the same Link / Keep both / Leave out vocabulary as every matched row. A weak
  Link decision carries the
  TWIN's id; `decision_commit`'s guard re-derives the ELIGIBLE twin set server-side
  (`_twin_eligible_ids` → `preview_ledger_check` on the same cached rows, exposed on the
  preview as `ledgerCheck.twin_eligible` — the match picker's option list) and accepts any
  (row, id) pairing in that row's eligible set, assigned winner or user-picked alternate —
  an id matching neither the engine verdict nor an eligible twin still raises
  ValidationError (tests/test_decision_commit_guard.py).
- **SI-56** (flat-list order retained; row/table anatomy superseded by SI-69–SI-80) GIVEN the
  preview step THEN statement rows render
  top-to-bottom as ONE flat list in the STATEMENT'S OWN ORDER (parse order = `rowIndex`;
  array order on client-mapped previews) — never regrouped into Matched/New sections, no
  band/section headers inside the list (a weak row between two strong rows stays between
  them); every statement row uses the handoff's semantic compact audit-row anatomy, while a
  matched row exposes SI-54's detail through its disclosure. The chips FILTER (single-select,
  All default, `aria-pressed`): `All N`, `Needs you n`, `Will link n`, `New n`, `Left out n`,
  and `Not on statement n` when populated. Counts derive from the same effective decision and
  structural predicates that render the rows; an L0 duplicate remains visible under All and
  Left out rather than being silently removed —
  each chip shows exactly the rows it counts, statement order preserved within; "Not on
  statement" focuses the LEDGER-ONLY block (which is otherwise visible under All only, SI-57
  internals unchanged); per-row decisions survive every filter switch. Waterfall jump-terms
  activate the corresponding chip instead of scrolling to a section (statement-only → New;
  ledger-rows-flagged / "See below ↓" → Not on statement; near-the-cut-off → Not on
  statement + force-expands the collapsed boundary bucket; matched-drift → Matched); a term
  whose chip has nothing to show falls back to All. A chip whose population empties from
  under it (last ledger-only row deleted) falls back to All rather than stranding a hidden
  list. No `role="tablist"`/`aria-selected` — the chips are a
  `<nav aria-label="Filter statement rows">` of `aria-pressed` buttons.
- **SI-57** GIVEN a LEDGER-ONLY row THEN it offers "Delete" (never "Remove") when it either
  has no twin or an already-booked twin (annotated with the counterpart's
  date/description/amount and drift when applicable), or "Show match ↑" (never Delete) when
  its twin resolves to a statement row with a live decision above (engine-claimed, needs-eyes,
  or an FX-pair leg) — Delete can never fire on a row whose only "duplicate" is still an
  undecided possibility. **AMENDED 2026-08-01 (SI-99): Delete is STAGED, not a write-through
  call** — see SI-99 below; a marked row STAYS in the list, marked and undoable, because it is
  still really there until Confirm.
- **SI-58** GIVEN the LEDGER-ONLY block THEN its header displays the signed net figure
  `uncovered_net`, read directly off the existing `ledgerCheck.totals[0]` wire field, and the
  verdict's ledger-residue line activates the "Not on statement" chip (SI-56 routing).
  - ~~"each non-identity waterfall term (not booked yet / ledger rows flagged above / near the
    cut-off / matched entries at original amounts) is a clickable jump-link that activates its
    corresponding filter chip"~~ and ~~"the statement-side figure `statement_unbooked_net`
    lives in the waterfall alone"~~ **SUPERSEDED 2026-07-31** — the waterfall and every
    jump-term inside it are gone; see SI-64.
  - ~~SI-56's "the waterfall's near-the-cut-off term force-expands the boundary bucket"~~
    **SUPERSEDED 2026-07-31** for the same reason — the bucket's own toggle is the only way in.
- **SI-59** GIVEN the preview step below 640px THEN a compact row gives decision, category, and
  amount distinct stacked positions, and an expanded matched row stacks On the statement →
  In your ledger → decision → After import. The workspace modal remains a focused canvas
  rather than becoming a bottom sheet, with no horizontal page overflow.
- **SI-60** SUPERSEDED visually by SI-74. GIVEN review, weak, or needs-eyes rows THEN Needs you
  opens the handoff's persistent, numbered decision-card queue. Its cards stay expanded after
  a choice, show the receipt and full After import consequence, and provide Undo; `1`/`2`/`3`
  operate the first undecided card outside text inputs. Bulk behavior elsewhere remains scoped
  to its own structural filter and never changes a needs-eyes row across bands.
- **SI-61** GIVEN the verdict zone WHEN ledger-only rows exist THEN it states the count and
  points at the LEDGER-ONLY section below ("N ledger entries from this period aren't on this
  statement… See below ↓" — no "duplicates to clean up" / "not on this statement" grouping
  language) — the detail (which rows, why, and their Delete/Show-match action) lives ONLY in
  the LEDGER-ONLY section itself, never duplicated in the verdict zone. A weak-consumed twin
  is NOT counted here (it is a MATCHED row now); a boundary-only book still reads clean.
- **SI-62** (projection behavior retained; three-column placement superseded by SI-75) GIVEN a
  matched row THEN its full-width After import result appears after the decision controls and
  is a LIVE projection of what the ledger will hold after commit, recomputed instantly when
  the row's action changes, mirroring
  `apply_import_merge` (facts="statement") EXACTLY:
  - **merge** → ONE record: the existing entry's identity (category/notes/payee — labels never
    change) with the statement's date and settled amount rendered as old→new deltas (unchanged
    fields stay quiet), the original charge gap-filled ONLY when the existing row lacks one
    ("+ original charge …"), and "cleared ✓ · reference attached" badges. Kind-gate mirror:
    merging into a `kind=transfer` target (echo) shows the transfer UNCHANGED + "linked ✓"; a
    transfer_pair / cross-kind merge shows the row unchanged + "cleared ✓ · reference linked"
    (date/amount never rewritten).
  - **skip** → ONE record: the existing entry, byte-unchanged, labeled "yours stays as-is".
  - **new** → TWO records stacked with the honest "2 records" count badge — the existing entry
    unchanged plus the statement row booked as its own transaction (the anti-double-booking
    honesty feature).
  - A statement-only audit row IS its projection (date, settled+original amounts, the enrich
    verdict's category or Uncategorized); a ledger-only row reflects
    reality (deleted → gone; untouched → unchanged).
  Pinned by `importHelpers.afterImport.test.ts` (pure projection) +
  `ImportModal.threeState.test.tsx` (render + live reactivity per band).
- **SI-63** — **SUPERSEDED 2026-07-29 by `SIUM-ALT-*`** (`docs/qa/acceptance/statement-import-unified-match.md`). The picker's option list
  is `flags[].alternatives` — server-chosen, best-first, each entry carrying its own
  `_existing_summary` — rendered VERBATIM. The client-side eligibility/steal/swap passes are
  deleted: the engine never offers an entry another row claimed, so every option is pickable and
  no option is ever dimmed. A pick is an ordinary `matchOverride`; an empty list renders no
  control at all. Original text below, for history.
  <br>(original text) (the match picker, owner amendment 2026-07-16 — "we actually should be able to
  choose between one or merge") GIVEN a MATCHED pair row whose statement row index has MORE
  THAN ONE eligible candidate in `ledgerCheck.twin_eligible` (or one eligible candidate that
  differs from the currently-resolved partner) THEN its LEDGER-NOW column carries a quiet
  "⇄ or match: N other entries ▾" affordance opening a listbox that names EVERY eligible
  entry as `date · note · amount (same amount | X.X% off)`, current partner marked
  (aria-selected ✓), a candidate currently paired to another row suffixed "— matched to
  another row"; keyboard contract: trigger `aria-haspopup=listbox`/`aria-expanded`, Escape
  closes ONLY the popover (the import flow stays), outside click closes.
  - Picking a candidate re-points the row's resolved existing EVERYWHERE at once: the
    ledger-now column, the headline evidence (recomputed against the picked entry's own
    figures when its `statement_twin` points elsewhere), the AFTER IMPORT projection, the
    outcome line, the ledger-only population (the un-picked former partner RETURNS below;
    the picked one leaves), and the commit payload's `existing_id` — decision path AND
    legacy onMerge path. An explicit pick also sets the row's action to Merge (affirmative
    "this IS that entry" intent; the chip shows pressed, toggle/re-pick freely).
  - Picking a candidate assigned to ANOTHER row steals it and the stolen row falls to its
    next free eligible candidate, best-ranked first (the crossed 14.22/14.23 pair re-pairs
    in one click); a row with no free candidate left degrades to a plain to-import row.
  - GIVEN a single eligible candidate that IS the current partner, or no `twin_eligible` on
    the wire, THEN no affordance renders. Server guard: commit accepts the engine-proposed
    id OR any id in that row's re-derived eligible set — anything else refused
    (`tests/test_decision_commit_guard.py`).
  Pinned by `ImportModal.matchPicker.test.tsx` (affordance gating, listbox a11y, re-point
  everywhere incl. both commit payloads, crossed-pair swap) +
  `importHelpers.verdict.test.ts` (`twinCandidateLine`/`weakPairEvidence`/`sameFigure`).
- **SI-64** (the two-question verdict, owner amendment 2026-07-16 — "after importing, is
  everything ok? would all statement transactions be in my ledger, and would the amounts
  match") GIVEN the preview step THEN the PRIMARY verdict is a projected post-commit block
  computed live from the current row choices (same reactivity as the After column — every
  chip/toggle/picker change updates it instantly), replacing the always-open math waterfall:
  - **Line 1 — coverage**: `✓ Every statement transaction will be in your ledger — N linked
    to entries you already had · M imported new` (+ `· D already booked` / `· F folded into
    transfers` when nonzero); a row's representative: merge → the matched entry, import-new
    → the new booking, skip WITH a resolved match → the matched entry, dups/absorbed FX
    legs covered by construction. WHEN any non-dup, non-pair-absorbed statement row is
    skipped WITHOUT a resolved match THEN `⚠ K statement transactions won't be anywhere
    after import — skipped with no ledger match`.
  - **Line 2 — amounts**: each REPRESENTED row's post-commit figure (merge → statement
    settled, unless the kind gate keeps the existing row's figures; new → statement figure;
    skip-with-match → the EXISTING entry's amount) totals against the statement's own
    activity for the same rows, per currency: `✓ Amounts will match — these transactions
    will total X in your ledger` or `⚠ Off by Y — K kept at your original figures (choose
    Merge to adopt settled)`. Uncovered rows are EXCLUDED from both sides (line 1's
    territory, never double-reported); the line renders only when something is represented.
  - **Line 3** (only when nonzero, pointer unchanged): `N ledger entries from this period
    aren't on this statement` + the `See below ↓` jump (SI-61's contract).
  - ~~The FULL math waterfall (SI-58 jump-terms intact) moves behind a collapsed `▸ Show the
    math` expander (`aria-expanded`, default collapsed; absent when the wire carries no
    totals).~~ **SUPERSEDED 2026-07-31** (owner: "let's not show the math for now, i don't
    know if it's really correct, everything looks always add up! doesn't make sense"). The
    waterfall's `drift` term is a RESIDUAL solved for server-side
    (`services/statements/ledger_diff.py`) as whatever makes the identity balance, so its
    "✓ adds up" tick could never fail and silently absorbed any real discrepancy. THEN the
    expander, the waterfall and its jump-terms render nowhere; the backend `totals` stay on
    the wire (they still feed the LEDGER-ONLY header subtotal and
    `tests/test_ledger_diff_covered_parity.py`). A no-period statement keeps a quiet
    cross-check-unavailable note.
  Pinned by `importHelpers.verdict.test.ts` (pure `coverageVerdict`/`amountsVerdict`:
  all-merge clean, skip-no-match, kept-estimate delta, kind-gate merge, per-currency
  buckets, cancel-out edge) + `ImportModal.verdict.test.tsx` (the four fixture cases at
  component level, live reactivity, and the waterfall's absence) +
  `ImportModal.threeState.test.tsx` (the LEDGER-ONLY subtotal + the verdict's own jump).

## One pipeline, every format (SI-65)

- **SI-65** (owner directive 2026-07-17: "EVERY import format must go through the ONE server
  decision pipeline — no client-side parallel brain") GIVEN ANY import source that reaches the
  import modal or the REST import endpoints THEN it previews via `decision_preview` and commits
  via `decision_commit` — bands (`match_batch`, weak tier included since 2026-07-29) + the match
  picker's `flags[].alternatives`, the pre-commit ledger check, minted `external_id` fingerprints (hard L0 dedup on every
  future re-import), guarded merges, a filed Statement record, and batch rollback — with NO
  per-format fork and NO client-side parse that books money. The matrix (each row pinned by
  `tests/test_import_pipeline_unification.py` unless noted):

  | Source | Parse | external_id shape | Bands + ledger check | Statement record + rollback |
  |---|---|---|---|---|
  | CSV manual-mapped | server, taught schema (0 LLM) | `csv:<sha>` (+`:N` twins) | ✓ | ✓ |
  | CSV auto (no mapping) | server, schema-infer / format memory | `csv:<sha>` | ✓ | ✓ |
  | XLSX (both routes) | server, sheet→grid, same as CSV | `csv:<sha>` (xlsx files keep `xlsx:` only via legacy history; new drafts hash under the taught/inferred schema's `csv` prefix — the modal always sent unmapped XLSX here already) | ✓ | ✓ |
  | OFX / QFX / QBO | deterministic adapter (`statements/structured.py`) | `ofx:<acct>:<FITID>` (byte-identical to legacy) | ✓ | ✓ |
  | QIF | deterministic adapter; `L` category resolves against the store | `qif:<sha>` (draft-time hash; the legacy account+category-keyed hash is retired — the near-dup band nets the one crossing re-import) | ✓ (window rarely — QIF declares no period) | ✓ |
  | MT940 | deterministic adapter; :60F:/:62F: → opening/closing + period | `mt940:<acct>:<sha>` (legacy-identical) | ✓ (reconciles against its own balances) | ✓ |
  | CAMT.053 | deterministic adapter; OPBD/CLBD + FrToDt; per-row currency routes to wallets | `camt:<acct>:<ref\|sha>` (legacy-identical) | ✓ | ✓ |
  | PDF | tier router (text/vision) — unchanged | `pdf:ref:…`/`pdf:<sha>` | ✓ | ✓ |
  | URL / Google-Sheet source | fetched server-side, then EXACTLY the flows above by detected format | as per format | ✓ | ✓ |

  The modal's ONLY commit path is the decisions-shaped `/api/statements/commit`; the one-shot REST
  `/api/statements/import` runs preview + a default-create decision commit (statement record + batch in
  its response). The old client `analyse()` preview brain and the `/api/transactions/bulk`
  commit workaround are DELETED from the import flow (the register's bulk endpoint itself is
  untouched). Known follow-up (out of scope here): the Lucius `import_transactions` tool still
  routes structured/mapped formats through the legacy per-format wrappers for its own
  preview/commit branches.

- **SI-65a** (reconcile gates completeness — batch-aware-dedup addendum) GIVEN a decision
  preview or commit THEN `BatchContext(complete=True)` is claimed ONLY when the statement's own
  reconcile identity passes (`ok`/`ok_if_flipped`) OR the format is a bank-complete machine
  export (OFX/MT940/CAMT) with a declared period and a clean parse (no guard warnings). A
  failed or unknown reconcile otherwise → `complete=False`: bands still compute, books-close
  promotion never fires on unproven books. Commit re-derives the same claim from the CACHED
  reconcile verdict. Pinned both ways:
  tests/test_import_pipeline_unification.py::test_gate_*.

## Teach lane: routing rules + upload-time instructions (SI-66 · SI-67)

- **SI-66** (import routing rules — "this is a transfer to X, always") GIVEN a spend rule
  whose action carries `route_to_account: <account_id>` (created via the review table's
  "✦ Always…" popover, `POST /api/rules`, or the `create_rule` tool) WHEN a statement row
  matches it (same match grammar as spend rules: description_contains / keywords / regex /
  account / kind / direction / min-max) THEN the row drafts as ONE two-leg TRANSFER at
  import draft-build, direction-aware — a money-out row books statement account → routed
  account, a money-in row the reverse (an observed shape: Meridian Bank
  `Ecom.EW….PWAV.….CashIn.…` = bank→PayWave wallet; `CashOut` = wallet→bank; ONE rule covers
  both directions) — and the routed draft then rides matching/bands/commit normally:
  - **external_id/fingerprint semantics UNCHANGED** — the routed draft keeps the plain
    draft's id, so a re-import L0-dedups and a file imported before the rule existed still
    dedups after it.
  - a routed draft duplicating an ALREADY-BOOKED transfer (e.g. alert-booked by the email
    agent, source=ai) flags `transfer_leg_echo` with the existing id; a blind commit
    default-SKIPS it; an explicit merge onto that id is ACCEPTED (re-derived server-side,
    never trusted from the caller) and links — legs stay as booked, reference absorbed.
  - **CHOKE INERTNESS (standing invariant)**: the create-choke's `apply_rules` stays
    LABEL-ONLY — a route_to_account rule is skipped WHOLESALE there (`create_transaction`
    with a matching description books unchanged: no re-kind, no half-applied label, no
    stop side-effect, no usage bump).
  - honest fallbacks, never a guess: a routed target that is missing, archived, the
    statement-side account itself, or holds a DIFFERENT currency → the plain draft stands;
    FX-pair rows are never routed (the pair machinery owns them).
  - the review UI marks routed rows with the `⇄ Books as transfer: A → B` chip; the teach
    popover proposes the derived match (payee, else the descriptor's stable merchant
    token) EDITABLE before saving, and its label twin ("Always use category…") creates a
    plain spend rule; confirm re-runs the preview against the CACHED parse (free).
  Pinned: `tests/test_import_routing_rules.py` (fail-first: PayWave rows draft
  expense/income without the rule, flip with it; commit books transfers; re-import
  dedupes; echo default-skip + accepted merge; choke inertness; fallbacks; store/REST
  round-trip) · FE `ImportModal.teach.test.tsx`.

- **SI-67** (upload-time instructions) GIVEN the choose step's optional textarea
  "Instructions for Lucius (optional)" with a "Remember for this source" checkbox beneath
  it (CHECKED by default) WHEN the user types guidance and previews THEN the text is
  REMEMBERED on the source's format profile (keyed like mappings — header-row fingerprint
  for tabular sources; a digit-free-lines template fingerprint for documents; a scanned
  PDF with no extractable text applies them in-request but cannot persist, said honestly
  by the save endpoint) and threaded into the TWO existing LLM calls ONLY — the
  row-extraction prompt and the category-hint batch — always FENCED (`core.untrusted`
  nonce boundary + confusable strip, never bare instructions) and capped at 1000 chars
  (422 past it, maxLength client-side). ZERO tool-scope changes; instructions can never
  trigger writes. Deterministic formats (csv/ofx/…) that make no LLM calls simply store
  them (they matter only for the hint batch if it runs). New imports of the same source
  auto-apply; the review meta line shows `N instruction(s) applied` (fresh LLM parse) /
  `saved for this source` (cached/deterministic) `· edit` — edit re-opens the textarea
  and saves back via `PUT /api/statements/{id}/instructions` (empty text clears; never
  re-parses, never re-bills).
  - **Remember unchecked = one-off** (`remember_instructions=false`, default true): the
    typed text applies to THIS preview identically (fenced into the same two calls) but
    is NEVER persisted — and instructions already saved on the profile survive untouched
    (one-off override; the request's text wins for this run). The meta line reads
    `N instruction(s) applied (this import only)` with NO edit link.
  - **Changed instructions bust the cached parse**: the statement record stamps the hash
    of the instructions its cached parse consumed (`parse_instructions_hash`; absent =
    parsed without any); a preview whose EFFECTIVE instructions (request-supplied —
    remembered or one-off — else profile-saved) hash differently invalidates the cache,
    re-parses under the new guidance (re-bills, honestly — the AI-cost fact shows it) and
    re-opens the once-per-file category-hint gate. Identical instructions stay
    cached/free. A one-off on a cached file therefore bills twice by nature: the
    following instruction-less preview re-parses again under the saved (or no)
    guidance — documented, not special-cased.
  - **Manual force re-parse**: the AI-cost fact carries a quiet `· Re-read` link (title
    says it re-runs the AI read and bills again) → re-runs the preview with
    `force_reparse=true`, treated exactly like an instructions mismatch (fresh parse,
    re-stamp, hint gate re-opened) even when instructions are identical.
  - **The persist contract — learn/lookup window invariant** (a reported bug, 2026-07-17: a
    Meridian Bank XLSX's 12-row preamble put the header at grid index 12, one past the old
    12-row lookup window — the profile was WRITE-ONLY: re-learned/re-billed every import,
    `uses` forever 0, and remembered instructions silently dropped): whatever header row
    `learn()` keys a profile by, `find_profile`/`_instruction_fingerprints` MUST be able
    to find. `_MAX_HEADER_SCAN_ROWS` (40) covers schema inference's whole 30-row sample
    (`tiers._MAX_SCHEMA_SAMPLE_ROWS`, pinned `>=`), and `learn()` REFUSES a header past
    the window (returns None — no memory beats broken memory). A pre-fix orphan profile
    self-heals on its next import: the widened scan finds it, no migration needed.
  - **Source-peek prefill** GIVEN a source with saved instructions WHEN the user picks
    its file on the source step THEN the choose step's textarea PRE-FILLS with the saved
    text — saved guidance is visible and editable, never invisibly applied. The dim
    `✦ Saved for this source` hint that used to accompany the prefill was REMOVED
    2026-07-27 (owner directive, matching the handoff exactly — `PARITY_AND_GAPS.md`
    deviation #10); the prefill itself is unchanged, only its own explanatory line is
    gone. `POST /api/statements/source-peek` is
    fingerprint-only (`profiles.peek_source`): ZERO LLM calls, no statement record, no
    writes; returns `{known, instructions, format_label}` and answers `{known: false}`
    for unknown/unreadable/absent sources (never an error the picker must handle;
    CSRF-guarded like the other import POSTs). Client-side the probe is best-effort: a
    failed/slow peek leaves the box empty, typing always wins over a late reply
    (stale-sequence + typed guards), the prefilled-state flag clears on the first edit
    (no longer visibly, since 2026-07-27 — see above), and edits re-save per the Remember
    checkbox exactly as typed text does.
  Pinned: `tests/test_import_instructions.py` (fenced in both prompts; profile persist +
  auto-apply next import; cap at store and prompt; deterministic store-only; PUT
  round-trip incl. clear/422/CSRF; remember=false never persists; one-off override keeps
  saved instructions + re-bills back; changed-instructions bust vs identical-free; REST
  `remember_instructions`/`force_reparse` form fields; deep-preamble persist + reuse +
  learn clamp + window-covers-sample invariant; peek known/unknown/no-writes; the
  type → preview(remember) → peek round-trip regression guard) · FE
  `ImportModal.instructions.test.tsx` (checkbox default-checked + flag sent; one-off
  meta copy without edit; Re-read forces the flag; peek prefill; edited prefill
  round-trips; failed peek harmless; slow peek never clobbers typing).

- **SI-68** (the Lucius draft-operations pass — free-text instructions become APPLIED
  changes, not just parse/label hints) GIVEN a preview runs WITH effective instructions
  (fresh or cached-parse-reused) THEN ONE bounded LLM call (`source=statement_lucius_pass`,
  a dedicated `task_models` key) reads the fenced instructions + the parsed rows + the
  owner's accounts (id/name/currency) + category labels and returns a CLOSED vocabulary of
  ops over draft row indexes — `route_transfer` (reshape the row into a two-leg transfer
  through the same `route_transfer_draft` core SI-66 routing rules use — direction-aware
  automatically off the row's own sign, so ONE op per row covers either direction),
  `set_category`, `skip` — each carrying the model's own `reason`. Ops are APPLIED
  DIRECTLY to the offered drafts (never a separate propose/confirm step — typing the
  instructions IS the ask); hard validation drops anything invalid SILENTLY (unknown
  account/category, an out-of-range or already-covered — L0-duplicate/skipped — row, more
  than one op per row) so a bad op costs nothing rather than mis-booking a row. No rule is
  minted — the pass's own memory is the SI-67 per-source instructions persistence, which
  re-feeds it on every future import of the source.
  - **Review surface**: a preview whose pass applied anything renders a calm strip above
    the table (`✦ Your instructions changed N row(s): R routed as transfers ·
    C recategorized · S marked to skip`) with an "Undo all", and each touched row carries
    its own `✦` provenance chip (the model's `reason` as its tooltip) plus a per-row
    `undo` link — a `route_transfer` op's chip supersedes SI-66's generic `⇄` routing-rule
    chip (same underlying reshape, richer provenance); a `skip` op renders a Skip/Include
    toggle identical to a needs-eyes row's, defaulting the row OUT of the commit until
    explicitly included.
  - **The choose-step textarea's honesty caption was REMOVED 2026-07-27** (owner directive,
    matching the handoff exactly — `PARITY_AND_GAPS.md` deviation #10). It used to read
    (dim, under the "Remember for this source" checkbox): "Notes that describe what a row
    really IS (like a transfer) get applied automatically — shown with a ✦ and a reason
    on the row, undoable." The behavior it described — a note silently reshaping a row,
    structurally — is UNCHANGED and still true; only the standing warning line is gone.
    The per-row `✦` provenance chip + undo (below) is the mechanism that still surfaces it
    after the fact, on the row itself.
  - **Undo is a server round-trip**: `DELETE /api/statements/{id}/lucius-ops[?row=N]`
    clears one row's op (or all) from the statement record — the pass's own hash stamp
    stays put ("not these" is remembered without re-billing) — then the caller re-previews
    against the cached parse (free); `decision_commit` re-applies whatever the LAST
    preview showed, never re-derived from a fresh LLM call, so what books is exactly what
    was reviewed.
  - **Cost contract**: bills once per (statement, instructions-hash) —
    `lucius_pass_hash` mirrors `parse_instructions_hash`'s stamp; an unchanged re-preview
    reuses the cached validated ops (zero LLM calls); changed instructions (or an explicit
    Re-read) re-open the pass exactly like SI-67's parse-cache bust.
  Pinned: `tests/test_import_lucius_pass.py` (fail-first: the exact reported-bug
  instructions text over PayWave CashIn/CashOut rows drafts plain expense/income without the
  pass, flips to direction-aware `route_transfer` ops with reasons and books real
  transfers with it; unknown account / out-of-range row / unresolvable category label /
  an already-booked-duplicate row's op all drop silently; a malformed reply yields zero
  ops and the preview still succeeds; compile-once caching + recompile-on-change via
  call-count on the LLM stub; one-off instructions still feed the pass without persisting;
  saved instructions auto-feed the pass on the next import; `set_category` + `skip` ops
  apply and the skip op default-skips at commit, overridable by an explicit decision) · FE
  `ImportModal.luciusPass.test.tsx` (honesty caption; applied strip with breakdown, absent
  when nothing applied; per-row chip per op type incl. suppressing the generic routed
  chip; skip toggle defaults to Skip; per-row and Undo-all both DELETE the right URL and
  re-preview).

### SI-99 — ledger-only Delete is staged and applied at Confirm (2026-08-01)

Owner: *"i have to delete every row by myself, can we also mark them as delete and only do in the
confirm button instead of delete one by one?"* (~8 duplicate ride charges to clear). Delete used
to fire `DELETE /api/transactions/<id>` on click — one irreversible round-trip per row, before
any commit, under a footer promising *"nothing is written until you confirm."*

- GIVEN a ledger-only entry WHEN Delete is tapped THEN the row is MARKED — no network call of
  any kind — showing a `Will delete` chip in place of `Ledger only`, a tinted row with a danger
  rail, the hint replaced by "Marked — this will be deleted from your ledger when you confirm.",
  and `Undo` in the slot `Delete` occupied. `Link to a new row` and `Leave it` are withdrawn
  while marked (you cannot link a row to an entry you are deleting).
- GIVEN marked rows WHEN the user abandons the import THEN nothing was written.
- GIVEN N marked rows THEN the footer reads `… · delete N from your ledger — nothing is written
  until you confirm`, with the destructive term emphasised in its own right (`.imp3-foot-del`),
  never folded into the neutral `link/import/leave out` run. "from your ledger" is what
  distinguishes it from "leave out" (which only means "don't import").
- GIVEN a statement with nothing to book but rows marked THEN Confirm is still enabled and reads
  "Confirm" — deletions are an outcome, not a no-op.
- GIVEN marked rows WHEN Confirm is tapped THEN ONE `window.confirm` names the count (never one
  per row; none at all when nothing is marked), and declining commits NOTHING — not even the
  import.
- GIVEN a confirmed commit THEN `onDecisionCommit`'s 4th argument is EXACTLY the marked ids, in
  panel order, each once; unmarked entries are absent.
- GIVEN `Undo` on a marked row THEN the chip, hint and full action vocabulary are restored and
  the id leaves the commit payload; other rows' marks are untouched.
- **Bulk**: with >1 entry the section carries `Mark all N for deletion` / `Clear marks` in the
  SAME shape as the Will-link bulk bar (`.imp-pair-bandhead` + `.imp-seg`, buttons only, no
  label of its own, acting on exactly the rows rendered beneath). No checkboxes, no selection
  mode — the buttons-only bulk bar and "no new selection paradigm" both hold. `Clear marks` is
  absent until something is marked.
- **Server guard** (`decision_commit(delete_ids=…)`): ids are validated against this statement's
  re-derived UNCOVERED set (`_manual_link_ids`), BEFORE any write — a foreign/fabricated/covered/
  out-of-window id refuses the whole commit with nothing written, and so does an id that is also
  a merge target in the same commit. Deletes execute LAST via `bulk_delete_transactions`, so a
  failure never costs the user the import they confirmed.
- **Failures surface**: `delete_errors` renders on the Done screen — "N marked transactions could
  not be deleted — <reason>. Everything else in this import was applied." The Booked line reports
  `N deleted from your ledger` only for deletions that actually happened. An id already deleted
  between preview and commit is reported as `already deleted`, not a hard refusal.

  Pinned: `tests/test_decision_commit_staged_delete.py` (guard refuses a foreign id with the
  victim byte-unchanged and nothing else written; fabricated id refuses; link-and-delete refuses;
  no `delete_ids` deletes nothing; confirm deletes exactly the marked rows and spares the rest,
  soft-delete, import still books; a repeated id deletes once; a row deleted between preview and
  commit is reported not raised; a Starting Balance refusal is reported while the import stands;
  one bad id does not take the good ones with it) · FE `ImportModal.stagedDelete.test.tsx`
  (20 cases across the four contracts: staged-writes-nothing, footer, confirm-sends-exactly,
  unmark-restores, bulk, failures-surface) · `ImportModal.threeState.test.tsx` (Delete marks
  rather than writing through; a marked row offers Undo).

## SI-99 · The entire descriptor is always readable

An observed issue (2026-08-09, a Meridian Bank statement): fourteen rows to categorize by hand, each named
`MBNK.58203947615.71298…`. The name sat in a fixed 190px rail that hard-ellipsizes, the only
escape was a native hover tooltip, and expanding a row showed the MATCH detail but never the
description — so for an unmatched row the full text had no surface at all. "i can't see the full
text of the row so it's hard to categorize them by myself … basically there must be a way that
users can see the entire description."

- GIVEN any statement row THEN it expands — including a duplicate, which previously had no
  expandable body and so no way whatsoever to read its text. The chevron is never hidden.
- GIVEN a row whose descriptor exceeds the name rail (`NAME_RAIL_CHARS`) WHEN expanded THEN the
  COMPLETE raw text renders wrapped (`.imp3-arow-full`, `overflow-wrap: anywhere`) — no ellipsis,
  no truncation, at any width.
- GIVEN a row whose descriptor already fits THEN it is NOT restated: the row shows it in full
  already, and printing it twice reads as a stutter. This is the same rule the ledger-only row
  has used since 2026-07-31, now one shared constant.
- **Rails**: the name rail is 252px and the decision rail 150px (was 190/212) — a net-zero
  rebalance, so the category and amount columns hold the same x as before and the list stays
  column-aligned. The decision rail wraps rather than overflows when a row carries both a
  decision chip and a `Needs your eyes` badge.

  Pinned: FE `ImportModal.fullDescription.test.tsx` (expanded row restates the descriptor
  byte-exact and ellipsis-free; a duplicate expands; a short descriptor is NOT restated and
  appears exactly once; the chevron is never hidden).
  NOT yet covered: `make qa-e2e` / `make ui-gate` / screenshot evidence for the new rails.
