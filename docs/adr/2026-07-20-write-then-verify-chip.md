# Write-then-verify: the chip replaces the ledger confirm card

- **Date:** 2026-07-20
- **Status:** Accepted
- **Supersedes (in part):** `2026-07-19-ledger-confirm-gate-one-tool-stored-args` — the
  MECHANISM it built is kept intact and re-armable; only the POLICY of which tools use it
  changes.
- **Decision gate / design of record:** `.claude/rfcs/2026-07-20-gate-on-irreversibility-write-receipt-chip.md`
  — this ADR records what SHIPPED; that RFC argues the selection rule (gate on
  irreversibility, not write-ness) against Hermes/OpenClaw prior art. Read it first.
- **Related:** `2026-07-19-confirm-gate-surface-matrix`,
  `2026-07-17-rule-override-audit-provenance`, `.claude/rfcs/turn-checkpoints.md`

## Context

The ledger confirm gate made `add_transactions` and `update_transactions` preview-then-commit
on any card-capable surface. It was built on good evidence: transcript forensics over 289 real
sessions showed 10.5% of write turns "succeeded and were still wrong", `update_transaction`
worst at 39 redos in 231 calls. The diagnosis was right — category precedence and the spend
rules engine resolve **silently** inside `create_transaction`, so the field the user actually
wanted is only discoverable after the write.

The prescription was wrong. Owner, over several messages:

> "do we really need to show these confirm cards at all though? ... i would rather see the chip
> of newly created/updated ones in the ai response and then tap on it to see the details"

> "bulks, i can understand / the rest, bullshit"

> "it's important for users to know what changes are going to [be] made and be able to verify
> right after that (not just chat but also via ui)"

The requirement the forensics identified is **legibility**, not **permission**. A card that
blocks every ordinary write pays for legibility with a round trip and a decision on each one,
and a decision asked too often stops being read — which returns the same failure mode by a
different route.

## Decision

**Ordinary ledger writes commit immediately. The assistant's reply carries ONE chip per turn
saying what landed.**

`GATED_TOOL_NAMES` is now empty. Five design points, each load-bearing:

**1. ONE chip per TURN, not per write.** A 12-row import is one chip. Collection happens at
the audit choke (`services/audit.py` — the one place every create/update/delete already
funnels through) into a per-turn ContextVar buffer (`core/write_receipt.py`), drained once at
turn end. No ledger write path was modified to make this work.

**2. DIFFERENTIAL, not a receipt.** The chip surfaces what is ANOMALOUS, not every field of
every row: `rule_override` (a rule DECIDED the stored category — the highest-value flag, since
that is precisely the silent resolution the forensics blamed), `new_payee` (a payee was minted
by this write), `uncategorized`, and `large_amount`. A correct, ordinary, fully-categorized
write raises no flags and renders as a bare count. Attention is a budget; the chip spends it
only on what a user could not have predicted from their own sentence.

**3. "Unusually large" is measured against THIS book.** `_LARGE_MULTIPLE` × the median of the
book's own recent write sizes — never a currency-denominated floor, which would read right in
USD and be nonsense in VND. Below `_MIN_SAMPLE_FOR_LARGE` rows the flag is **skipped**, not
guessed: "unusual" is not a claim we can honestly make from three transactions.

> **Amended 2026-07-20 (two later changes, recorded here so this file is not read as current
> truth on either point).**
>
> **(a) UNDO WAS REMOVED.** Owner: *"please stop having these undo! just show!"*. The chip
> reports; it does not reverse. Every paragraph below about the undo state machine, the
> `undoable` stamp, "Undo window passed", Redo, and the 409-retryable path describes machinery
> that **no longer exists** — the `undoable` field is gone from the wire shape and there is no
> Undo control. The writes stay fully recoverable through the turn checkpoint,
> `txn_audit.json` and soft-delete; that is simply not this component's job, and the four
> states needed to describe an expiring undo honestly were more machinery than the affordance
> was worth. The receipt is still stored on the checkpoint index entry (decision 4 below) —
> that pairing is what makes it survive a reload, and it is unchanged.
>
> **(b) THE CHIP IS NO LONGER LEDGER-ONLY.** See
> `docs/adr/2026-07-20-generalize-write-receipt-beyond-transactions.md`: `record_write` gained
> an `entity_type`, ten more chokes report, and every row renders as a real mention chip. The
> transaction path through this ADR's design is unchanged.
>
> **(c) THE CHIP IS NOW ENTITY-ONLY (2026-08-01).** The differential warnings and all
> receipt-specific domain metadata were removed by owner direction. Collapsed receipts show
> counts and entity types; expanded receipts show only entity chips and operation glyphs.
> Persisted legacy warning/amount fields are tolerated but ignored by the frontend.

**4. Undo is the turn checkpoint.** `ai/checkpoint.py` already captures the pre-write bytes of
every file a turn touches. The receipt is stored **on that checkpoint's index entry**, so the
chip and the thing that reverses it are one record. This is why the chip survives a reload
(`turn_receipts`, read back exactly as the Restore anchor already is) and why undo is not
bounded by the confirm token's 15-minute TTL — that TTL was correct for a token gating a
FUTURE write, and wrong for reversing a past one.

**5. Confirmation survives where the MODEL chose the SET of rows.**
`delete_transactions_where` and the global `apply_payee_rules`. "This matched 847 rows" is
information available no other way, so it cannot be verified from a chip afterwards — unlike
"did you record the $12.40 I just told you about", which can. Neither was ever in
`GATED_TOOL_NAMES` (they enforce their own `confirm=true` and are hardline-blocked unattended),
so emptying that set does not weaken them.

## Consequences

**Undo is DURABLE, not ETERNAL — and the original design brief said "must never expire".**
This is the one place the implementation knowingly diverges from the instruction, because the
instruction is not achievable as stated on the mechanism it also mandated: checkpoints live in
a ring capped at `MAX_CHECKPOINTS = 50`, so a turn's checkpoint is eventually evicted and its
restore 404s. Rather than claim otherwise, the chip degrades honestly — it shows "Undo window
passed" instead of a button that would fail, reached either from a backend `undoable: false`
stamp or from a click-time 404. Undo is what stands in for consent in this design, so a promise
it cannot keep is the one thing it must not make. **Raising or removing the checkpoint cap is
a storage-policy decision left open, not settled here.**

**The confirm mechanism is kept, not deleted.** Stage/redeem, the stored-args security property
(a card showing $12.40 cannot commit $1,240), the outcome-drift refusal and the connector
redemption path are all intact and fully tested. Rollback is adding a name back to
`GATED_TOOL_NAMES`; nothing else. Its tests were re-pointed to exercise the mechanism against a
patched set, with the production policy pinned separately — so neither half can rot silently.

**Collection is closed by default.** A REST edit from the register, an import, a migration and
a test all record nothing, because none of them opened a receipt. "The chip shows what THIS
turn wrote" is true by construction rather than by filtering timestamps afterwards.

**Unattended writes are unaffected.** The per-agent write envelope (`ai/_confirm_gate.py`) and
the hardline unattended block are orthogonal to this and untouched.

## Alternatives rejected

**Keep the card for bulk writes only** ("bulks, i can understand"). Tempting, and the owner
would have accepted it. Rejected because it splits the model's write path on row count — the
same tool behaves differently at N=1 and N=2, which is exactly the kind of arity-dependent
surprise this codebase has spent four naming lanes removing. The chip already reports a bulk
write as one card-sized summary with an exact total; the legibility need is met without the
block.

**A separate undo store keyed to the chip.** Rejected on the brief's own instruction, and on
merit: it would duplicate checkpoint semantics, need its own retention policy, and drift.

**Emit the chip from the ledger tools.** Would have needed edits in
`ai/tools/ledger.py`/`services/ledger.py` and would have missed every write not routed through
those specific tools. The audit choke already sees all of them.

## Agent provenance

Built by the `lane/write-verify-chip` lane. Two decisions made mid-implementation without
asking, both recorded above because a reviewer would otherwise reasonably assume they were
oversights: (a) the "undo never expires" divergence and its honest-degradation fallback,
(b) not special-casing bulk despite the owner explicitly allowing a card there.

One defect found and deliberately **not** fixed (it is outside this lane and
`ai/tools/ledger.py` is owned elsewhere this run): `delete_transactions_where` has **no
interactive confirmation at all** — only the unattended hardline block. The brief assumed it
already confirmed. Filed on `docs/launch/BOARD.md` as an inbox line.
