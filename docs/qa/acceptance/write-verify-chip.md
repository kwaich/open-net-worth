# Acceptance — Write-then-verify chip (what a turn wrote to your ledger)

The chip that replaced the per-write confirm card (ADR `2026-07-20-write-then-verify-chip`).
Verify on the running app via `make qa-e2e` / `make qa-cu`, never `:5174` / `:8080` directly.

**Why this exists (the bar to clear):** the confirm card asked permission BEFORE every ordinary
ledger write. Owner: *"do we really need to show these confirm cards at all though? ... i would
rather see the chip of newly created/updated ones in the ai response and then tap on it to see
the details"*, *"bulks, i can understand / the rest, bullshit"*, and — the requirement that
survives — *"it's important for users to know what changes are going to be made and be able to
verify right after that (not just chat but also via ui)"*.

So the bar is **verifiability after the fact**, not approval before it. **A chip that merely
says "done" does not pass this file** — every criterion below is about the user being able to
see, reach, and reverse what actually landed.

Backend: `core/write_receipt.py` (collection) · `services/write_receipt.py` (the differential)
· `services/mentions/labels.py` (server-side `(type, id)` → `MentionRow`) ·
`ai/checkpoint.py::turn_receipts` (persistence). Frontend:
`components/chat/WriteReceiptChip.tsx`. SSE event: `write_receipt`.

**The chip is MULTI-TYPE** (2026-07-20 generalization). It started ledger-only; it now reports
any entity type a turn wrote — transactions, envelopes, categories, lots, disposals, grants,
vests, income events, agents, scheduled tasks, alerts, skills — with every row rendered as a
real mention chip by the same `<Chip>` the `@`-picker uses. Which chokes report is a deliberate
list (`tests/test_write_receipt_multi_type.py::RECORDED_ENTITY_TYPES`), not a sweep: a domain
with an existing `apply_*_change` choke is instrumented there; a domain without one (accounts,
payees, rules, watchlist, …) is DEFERRED rather than instrumented at its call sites.

**There is NO Undo** (owner, 2026-07-20: *"please stop having these undo! just show!"*). The
writes stay recoverable via the turn checkpoint, `txn_audit.json` and soft-delete — that is
simply not this component's job, and a button with an expiry (`MAX_CHECKPOINTS` evicts) the
rest of the system does not have was a promise it could not keep.

## WVC1 — The write happens; the chip reports it

- **WVC1.1** GIVEN an interactive chat turn WHEN the user asks Lucius to book a transaction
  ("log $13.80 Grab ride from Checking") THEN **no confirm card appears**, the row is written
  immediately, and the reply carries a chip reading "1 transaction added".
- **WVC1.2** GIVEN a multi-row booking (a pasted batch of 20 rows) THEN **exactly ONE chip**
  renders for the whole batch — never one per row — reading "20 transactions added".
- **WVC1.3** GIVEN a turn that edits an existing transaction THEN the chip reads "1 transaction
  edited". The head ALWAYS names the type: a bare "1 edited" cannot say which of several types
  changed, which is the ambiguity the multi-type generalization had to fix.
- **WVC1.4** GIVEN a turn that both books and edits ONE type THEN the chip reads
  "N transactions changed" — a group with several operations must not name just one of them.
- **WVC1.7** GIVEN a turn that writes SEVERAL types (books 3 rows and assigns an envelope) THEN
  ONE chip renders reading "3 transactions added · 1 envelope edited", most rows first; past
  two groups it appends "+N more".
- **WVC1.8** GIVEN a turn that records a vest (which cascades an income transaction AND a lot)
  THEN each entity appears EXACTLY ONCE — a choke reports only the entity it is the choke for.
- **WVC1.5** GIVEN a turn that writes NOTHING to the ledger (a question, a read-only tool call)
  THEN **no chip renders at all**.
- **WVC1.6** GIVEN a turn that wrote rows THEN the register/Accounts tab shows those rows
  **without a manual refresh** (the existing `data_changed` freshness path still fires).

## WVC2 — ENTITY-ONLY

- **WVC2.1** GIVEN any write THEN the collapsed chip shows only counts, entity types and the
  create/edit/delete verb — no warnings or domain metadata.
- **WVC2.2** GIVEN a persisted legacy receipt containing warning or amount fields THEN those
  fields are ignored rather than rendered.

## WVC3 — Tapping the chip opens the detail

- **WVC3.1** GIVEN a chip WHEN it is tapped THEN it expands **in place** to the written rows,
  each rendered as a real mention chip (glyph, type colour, entity name) beside an operation
  glyph; tapping again collapses it. No sublabel, amount, date, category, account, budget month,
  or warning is shown.
- **WVC3.2** GIVEN an expanded chip WHEN a transaction row is tapped THEN the app navigates to
  Accounts and opens **the real transaction detail drawer** for that row (`/accounts?txn=<id>`)
  — the same drawer the register opens, not a chat-only lookalike.
- **WVC3.5** GIVEN a row whose TYPE has no deep link yet (only `transaction`, `account` and
  `commitment` have routes that declare the search param) THEN the chip still renders and
  simply does not navigate — never a link to a screen that would strip the param and land the
  user with nothing selected after promising a specific row.
- **WVC3.6** GIVEN a row whose entity can no longer be read back THEN it still renders, labelled
  by its TYPE ("Transaction") rather than an invented name, and marked stale — dropping it
  would leave the counts disagreeing with the list.
- **WVC3.3** GIVEN a write of more rows than the chip samples (>12) THEN the expanded detail
  states "Showing 12 of N" with N the true total — a partial view must never be mistaken for
  the whole write.
- **WVC3.4** GIVEN a row that can no longer be resolved THEN the detail says so explicitly,
  rather than rendering an empty box that would read as "nothing happened".

## WVC4 — Recovery is real, but it is NOT on this chip

- **WVC4.1** GIVEN any chip THEN it offers **no Undo and no Redo control** (owner, 2026-07-20).
  The chip reports; it does not reverse.
- **WVC4.2** GIVEN a turn that wrote rows THEN a turn checkpoint still captured the pre-write
  bytes of every file it touched, so the writes remain recoverable through Checkpoints —
  recovery just is not reached from here.

## WVC5 — Reload safety

- **WVC5.1** GIVEN a turn wrote rows WHEN the page is reloaded THEN the chip is still on that
  turn's reply, with its counts, flags and rows intact.
- **WVC5.3** GIVEN a conversation older than the checkpoint ring THEN its chips are absent
  rather than present-but-broken.

## WVC6 — Confirmation survives where the MODEL chose the row set

- **WVC6.1** GIVEN the user asks Lucius to apply payee rules across the whole ledger THEN it
  still requires explicit confirmation (`confirm=true`) — the blast radius is a SELECTION
  nobody can verify after the fact ("this matched 847 rows" is available no other way).
- **WVC6.2** GIVEN an unattended agent run THEN `delete_transactions_where` and
  `apply_payee_rules` are still refused outright, and per-agent write-envelope enforcement is
  unchanged.

## Empty / loading / error states

- **Empty:** a turn with no ledger writes renders no chip (WVC1.5); a chip whose rows cannot be
  resolved says so (WVC3.4).
- **Loading:** the chip renders only after the turn completes; there is no in-flight state.
- **Error:** a type whose store read fails degrades to a STALE row for that type alone and never
  sinks the receipt; an unregistered type is dropped rather than rendered blank.
- **Unknown flag from a newer backend:** rendered as its raw key rather than dropped — a
  silently swallowed anomaly is the one failure this component must not have. **An unknown
  TYPE** is treated the same way: it renders with its raw type name, never vanishes.

## Verification status

Unit/component coverage is green and named below. **`make qa-e2e` was NOT run for this lane**
(no browser harness was available in the worktree), so WVC1–WVC6 are **not** yet signed off
against the running app — this file is the contract, not evidence that it passed.

- `tests/test_write_receipt.py` — 19 tests: collection closed-by-default + nesting, one chip
  per turn, every WVC2 flag rule including the two negative cases (WVC2.3, WVC2.6), the
  checkpoint pairing and its reload read-back.
- `tests/test_write_receipt_multi_type.py` — 23 tests for the generalization: the transaction
  path byte-identical through the UNEDITED audit choke, closed-by-default per type, every
  recorded type is a registered mention type, cascades recorded once (WVC1.8), the multi-type
  head groups (WVC1.7), the rare-type sample guarantee, and the checkpoint-inclusion check
  that stops a domain from producing a receipt that `finalise` silently discards.
- `frontend/src/components/chat/WriteReceiptChip.test.tsx` — 15 tests: WVC1.1/1.3/1.4/1.7,
  WVC2.1/2.2, WVC3.1–3.6.
- `tests/test_ledger_confirm_gate.py` — the confirm MECHANISM stays fully covered against a
  re-armed tool set, with the now-empty production policy pinned separately.
