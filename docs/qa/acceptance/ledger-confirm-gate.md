# Acceptance — Ledger confirm gate (preview before a transaction is written)

The Lucius chat card that previews a ledger write and commits only on the user's ✓. Verify on
the running app via `make qa-e2e` / `make qa-cu`, never `:5174` / `:8080` directly.

**Why this exists (the bar to clear):** transcript forensics over 289 real sessions found
10.5% of write turns "succeeded and were still wrong" — `update_transaction` alone had 39
redos in 231 calls (17%), usually with NO error at all. The cause is that category precedence
(explicit > rule > payee-default > None) and the spend-rules engine resolve *silently* inside
`create_transaction`. **A card that merely echoes the model's input back does not pass this
acceptance file** — every criterion below is about showing the RESOLVED outcome.

Backend contract: `services/ledger_preview.py`; gate `ai/tools/_confirm_gate.py`; staged args
`db/pending_plan_store.py`. Change types: `txn_create`, `txn_update`, `txn_bulk`.

## LCG1 — The card appears instead of a silent write

- **LCG1.1** GIVEN an interactive chat turn WHEN the user asks Lucius to book a transaction
  ("log $13.80 Grab ride from Checking") THEN a `ChangePreview` card headed "Book transaction"
  renders, the tool result says `pending_confirmation`, and **no row appears in the register**
  until ✓ is tapped.
- **LCG1.2** GIVEN the user asks Lucius to edit an existing transaction THEN a card headed
  "Update transaction" renders showing a before → after diff, and the stored row is unchanged
  until ✓.
- **LCG1.3** GIVEN a multi-row booking (a pasted batch of 20 rows) THEN **exactly ONE card**
  renders for the whole batch — never one card per row.
- **LCG1.4** GIVEN a card WHEN ✗ / Dismiss is tapped THEN nothing is written, and the card
  renders its disabled dismissed state (survives a reload / conversation switch).
- **LCG1.5** GIVEN a draft that cannot be booked (unknown category, missing leg amount,
  a Starting Balance row) THEN the tool returns the SAME validation error it returned before
  the gate existed — no card is staged for a change that could never commit.

## LCG2 — The card shows the RESOLVED outcome, not the input

- **LCG2.1** GIVEN a spend rule that matches the draft (e.g. "Grab → Transport") WHEN the card
  renders THEN it shows the category **Transport** (the rule's), not the model's input, with a
  provenance chip naming the rule: "set by rule: Grab → Transport".
- **LCG2.2** GIVEN an `override_existing` rule and a draft that *did* specify a category THEN
  the card shows the rule's category winning — the live 2026-07-17 incident shape, where a
  GrabFood order specified "Daily Meals" and silently stored "Transport".
- **LCG2.3** GIVEN a payee with a per-kind default category and a draft with a blank category
  THEN the card shows that category with the provenance "<payee>'s default".
- **LCG2.4** GIVEN a draft with no category and no rule/payee match THEN the card shows
  **"Uncategorized"** as a visible state with provenance "none" — never an empty cell. A user who
  expected a category has to be able to see that none was applied.
- **LCG2.5** GIVEN a draft naming accounts THEN the card shows account **names**, not ids.
- **LCG2.6** GIVEN a transfer THEN the card shows **both legs** with a direction indicator.
- **LCG2.7** GIVEN a foreign-currency leg THEN the card shows the base-currency value and, when
  the leg currency differs from base, the FX rate that will be stamped.
- **LCG2.8** GIVEN a split allocation THEN the card lists each split line with its category
  **label** and amount.
- **LCG2.9** GIVEN a merchant not yet in the payee registry THEN the card says the payee will
  be created — and **the payee is NOT created** by rendering the card (see LCG4.1).
- **LCG2.10** GIVEN a leg dated before its account's tracking start THEN the card surfaces that
  warning BEFORE the write, not in the post-write result.

## LCG3 — Confirming commits exactly what was shown

- **LCG3.1** GIVEN a card WHEN ✓ is tapped THEN the row commits with the values **on the card**
  and the register/net worth refresh.
- **LCG3.2** GIVEN a confirmed card WHEN the ✓ is somehow re-sent (double-tap, replayed prose)
  THEN the second attempt REFUSES — the transaction is booked exactly once.
- **LCG3.3** GIVEN a bulk card WHEN ✓ is tapped THEN all N rows commit in one batch (the card's
  sample is capped at 12 rows for display; the count and totals cover all N).
- **LCG3.4** GIVEN a LEGACY MINTED-TOKEN preview card left unconfirmed for more than 15 minutes
  WHEN ✓ is tapped THEN it refuses with a message telling the model to re-preview, and nothing is
  written — a stale *preview* must re-resolve against the current book, not fire yesterday's
  numbers.
- **LCG3.4a** GIVEN an AWAITING-APPROVAL card (the live permission gate) left unconfirmed for any
  length of time — 20 minutes, overnight, across a server restart — WHEN ✓ is tapped THEN it
  EXECUTES the arguments the card displayed. An approval is a question put to a human and never
  expires (ADR `2026-07-30-approvals-never-expire`). If it cannot be redeemed at all (already
  answered elsewhere, or superseded by a later message) the error names the card and the card
  reverts to unanswered — it must never show "✓ Approved" above a red error.
- **LCG3.5 (security)** GIVEN a staged card for $13.80 WHEN the confirming call carries
  different transaction fields (a model reconstructing from history, or a prompt injection)
  THEN **the stored args win** — $13.80 commits, the substituted values are discarded.

## LCG4 — Previews must not write (ADR 2026-07-18)

- **LCG4.1** GIVEN a preview for a brand-new merchant THEN **no payee row is created**. The
  card reports the intent (`is_new`) instead. (This is the exact hazard the ADR was written
  for: `propose_position_update` used to mint accounts at preview time.)
- **LCG4.2** GIVEN any preview that the user then dismisses THEN the data directory is
  byte-unchanged apart from the staged-plan queue (`pending_plans.json`, a runtime file that
  is checkpoint-EXCLUDED and holds no user data).

## LCG5 — Scope: only a surface that can render AND apply the card is gated

- **LCG5.1** GIVEN an unattended run (scheduled task, agent — e.g. the
  `email-transaction-logger`) THEN the gate is OFF and the write commits as before. Nobody is
  present to tap ✓, so gating would stop the automation rather than make it safer.
- **LCG5.2** GIVEN the REST API (`POST /api/transactions`) or any import path THEN behavior is
  unchanged — the gate lives on the AI tool surface only.
- **LCG5.3 (regression — found by security review)** GIVEN a transaction booked over a CHAT
  CONNECTOR (Telegram — the only bidirectional chat connector; Slack/Discord/WhatsApp were
  removed in the 2026-08 channels scope-down) THEN it commits exactly as before, with no
  confirm card. That surface renders a change via `format_change_summary` and applies it via
  `chat/confirm.apply_change`, neither of which understands these change types — so gating it
  would break booking over chat entirely. **An inbound chat turn is deliberately NOT marked
  unattended, so "is this interactive" is the WRONG test; the gate keys on the card-capable
  surface (`web`) instead.**
- **LCG5.4** GIVEN an inbound MCP `tools/call` or a REST agent-finding apply naming a gated
  ledger tool THEN it writes as before — it must never receive a "staged, not written" result
  that its caller reads as success.
- **LCG5.5** GIVEN a `run_skill` playbook (e.g. `statement-import`) THEN its writes are NOT
  gated — a scripted sequence must not be interrupted per write. GIVEN an `@agent` responder
  turn in web chat THEN writes ARE gated, matching the responder contract.
- **LCG5.6** GIVEN `delete_transactions` THEN it is NOT gated in this slice (the measured
  redo evidence is on add + update). GIVEN `update_transactions` (2026-07-19 merge of
  `update_transaction` + `bulk_update_transactions`) THEN it IS gated for every shape it
  accepts — single row, the `updates` array, and the legacy `transaction_ids` shared-change
  shape alike; the pre-merge `bulk_update_transactions` used to write immediately with no
  card, which the merge deliberately closed (see `docs/adr/2026-07-19-merge-update-transaction-tools.md`).

## LCG7 — Confirming/dismissing a card echoes a compact chip, never the handshake plumbing

- **LCG7.1 (owner-reported, 2026-07-19)** GIVEN a card WHEN ✓ is tapped THEN the new chat
  bubble that appears (the synthetic turn `ChangePreview.handleConfirm` sends) renders as a
  compact "✓ Confirmed" — never the raw tool name (`update_transactions`/`add_transactions`)
  or the `confirm_token` value. The model's own copy of that turn is unchanged (the token is
  still what the server pops to redeem the stored args, per LCG3.5/LCG6.1) — only the DISPLAY
  is compacted, at render (`frontend/src/utils/untrusted.ts`'s `stripConfirmHandshake`,
  applied in `MessageBubble.tsx`).
- **LCG7.2** GIVEN N cards resolved in the same assistant turn (buffered into one newline-
  joined message, e.g. a batch of per-row confirms) THEN the echoed bubble shows ONE compact
  line ("✓ Confirmed ×N"), not N raw lines.
- **LCG7.3** GIVEN a card WHEN ✗ / Dismiss is tapped THEN the echoed bubble reads "✗
  Dismissed" — same treatment as confirm.
- **LCG7.4** GIVEN a conversation containing a resolved confirm/dismiss echo WHEN the page is
  RELOADED (a fresh IndexedDB read, or — on a cache miss — the backend's
  `rehydrate_messages` reconstruction) THEN the bubble still renders compact, not the raw
  handshake — the strip runs at render regardless of which path supplied `Message.text`.

## LCG6 — The token binds the outcome, not just the arguments

- **LCG6.1 (security)** GIVEN a card showing category "Uncategorized" WHEN a spend rule is created
  after the card is shown but before ✓ is tapped THEN the confirm REFUSES and writes nothing,
  telling the user/model to re-preview — the row never commits under a category the card did
  not show. (`create_rule` is ungated and reachable in the same turn as the confirm, so args
  alone are not a sufficient binding.)
- **LCG6.2** GIVEN nothing changed between preview and ✓ THEN the confirm commits normally.

## Browser-checked criteria (machine-parseable)

The groups above are the full contract; most of it is pinned by pytest
(`tests/test_ledger_confirm_gate.py`). The criteria below are the subset that can only be
proven **in the running app** — a real Lucius turn, the SSE `proposed_change`, and the card's
own buttons — so they carry stable ids that `qa/browser-use/specs/*.yaml` bind to via
`acceptance_ids`, and `make qa-acceptance-check` reports their coverage. They restate, in the
parser's GIVEN/WHEN/THEN form, criteria already stated as prose above (cross-referenced).

### LCG-CARD-RENDERS-ON-TURN

- GIVEN an interactive web chat turn with Lucius (cross-ref LCG1.1)
- WHEN the user asks Lucius to book a single transaction in prose
- THEN a confirm card renders in the chat with a ✓/confirm and a ✗/dismiss affordance, and no
  new row appears in the register until ✓ is tapped

### LCG-CARD-SHOWS-RESOLVED-CATEGORY

- GIVEN a draft whose category is decided by the resolver rather than by the user (cross-ref
  LCG2.1, LCG2.3, LCG2.4)
- WHEN the confirm card renders
- THEN it shows the RESOLVED category label — "Uncategorized" when none applied, never an empty
  cell — together with the provenance of the tier that decided it, naming the rule by name
  when a spend rule set it

### LCG-CARD-SHOWS-ACCOUNT-NAMES

- GIVEN a draft whose legs reference accounts (cross-ref LCG2.5)
- WHEN the confirm card renders
- THEN each leg shows the account's NAME, never a raw `acc_...` id

### LCG-CARD-SHOWS-TRANSFER-LEGS-AND-BASE

- GIVEN a transfer between two accounts (cross-ref LCG2.6, LCG2.7)
- WHEN the confirm card renders
- THEN BOTH legs are shown with a direction indicator, and the base-currency value that will
  be stamped is shown as a formatted money amount, never raw JSON

### LCG-CONFIRM-COMMITS-EXACTLY-ONE

- GIVEN a rendered single-transaction confirm card (cross-ref LCG3.1)
- WHEN the user taps ✓
- THEN exactly ONE transaction is committed, its stored values match what the card showed,
  and the register reflects it

### LCG-DISMISS-WRITES-NOTHING

- GIVEN a rendered confirm card (cross-ref LCG1.4)
- WHEN the user taps ✗ / Dismiss
- THEN nothing is written, the register is unchanged, and the card renders its dismissed state

### LCG-BULK-ONE-CARD-TRUE-COUNT

- GIVEN a multi-row booking request (cross-ref LCG1.3, LCG3.3)
- WHEN the confirm card renders
- THEN exactly ONE card renders for the whole batch, never one per row, and it states the TRUE
  total row count even though the rendered sample is capped at 12 rows

### LCG-CONFIRM-ECHO-IS-COMPACT

- GIVEN a rendered confirm card (cross-ref LCG7.1, LCG7.4)
- WHEN the user taps ✓ and a new chat bubble appears for that tap
- THEN the bubble reads a short compact confirmation (e.g. "✓ Confirmed") — it must NOT
  contain a tool name (`update_transactions`, `add_transactions`) or the word `confirm_token`
  — and reloading the page still shows the compact form, not the raw handshake

## States

- **Empty:** a bulk card with zero valid rows never renders — validation fails first (LCG1.5).
- **Loading:** the card renders synchronously from the tool result; no separate fetch.
- **Error:** an unknown/used/expired token renders no card and returns a prescriptive refusal
  naming the next action ("call it again without confirm_token to show a fresh preview").
- **Money formatting:** every amount on every card renders through the app's money formatter —
  a money card must never degrade to raw JSON.
