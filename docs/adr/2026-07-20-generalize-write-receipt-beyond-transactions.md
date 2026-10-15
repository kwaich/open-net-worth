# Generalize the write-receipt chip beyond transactions

- **Date:** 2026-07-20
- **Status:** Accepted
- **Supersedes in part:** `2026-07-20-write-then-verify-chip.md` (the ledger-only scope; its
  transaction path, closed-by-default collection and checkpoint pairing are unchanged)

## Context

The write-then-verify chip answers "what did that turn change to my book?" — but it could only
answer it for TRANSACTIONS. A turn that assigned an envelope, recorded a vest, created an agent
or cancelled a scheduled task produced **no chip at all**, so the one surface that exists to
make a write verifiable was silent for every non-ledger write the assistant can make. The head
compounded it: "1 edited" never named WHAT was edited, which is harmless when everything is a
transaction and ambiguous the moment it is not.

Three seams could have carried this.

**Rejected — the `mutates=` tool-decorator seam.** It already knows a turn's touched DOMAINS,
which sounds like the same information. It is not: a domain is not an entity, `mutates=` says
"the ledger changed" and cannot say WHICH rows, and it is declared per-handler across 167
handlers — a drift surface that is already the demonstrated failure mode this codebase fixed by
inverting `tests/test_tool_domains.py` to require explicit classification.

**Rejected — a JsonStore byte-diff seam.** Structurally complete (every write funnels through
`_save_raw`) and therefore tempting. But it would diff whole files on every write, on the write
path, to produce something the chokes already have in hand for free; and a diff of stored bytes
recovers ids without recovering INTENT — it cannot distinguish "the user assigned this
envelope" from "the auto-assign fan-out touched it", which is exactly what `meta["verb"]`
carries.

**Chosen — generalize the existing per-choke ContextVar seam** (`core/write_receipt.py`) by
adding an entity TYPE, and render every row as a real mention chip whose label is resolved
SERVER-SIDE at turn end.

## Decision

**1. One keyword-only parameter, defaulted.** `record_write(op, entity_id, *, entity_type=
"transaction", meta=None, rule_applied=None)`. The default and the `rule_applied` alias exist
for a specific reason, not tidiness: `services/audit.py` is edited concurrently by another
lane, and both of its call sites had to keep working **with zero edits**. The transaction path
is therefore byte-identical, and that is pinned by a test that asserts on what the UNTOUCHED
audit choke emits (`test_the_audit_choke_still_records_a_transaction_with_NO_argument_changes`)
rather than on a hand-built fixture.

**2. `op` is a CLOSED vocabulary: `create` | `update` | `delete`.** A domain verb (acquire,
dispose, vest, merge, assign, expire, pause) maps onto one of those three at its own choke and
rides in `meta["verb"]`. The chip head must never grow a per-domain verb vocabulary the UI has
to learn — that is how a two-type feature becomes a twelve-branch renderer.

**3. `entity_type` MUST be a registered mention type.** A row with no chip renderer is worse
than no row, so an unregistered type is dropped by the reader, and the pairing is asserted by a
test with an explicit table (`RECORDED_ENTITY_TYPES`) rather than a grep — adding a domain is a
deliberate edit there, which is the moment to notice it also needs a route and a checkpoint
inclusion.

**4. Labels are resolved server-side, once, by `services/mentions/labels.py::receipt_rows`.**
This is the piece that unblocked mention chips, because `(type, id)` could not yield a label
anywhere in this system: `resolve_mentions()` ECHOES the caller's label rather than deriving
one (and caps at 12, and can splice network context on the chat path), search never matches on
an id, and the per-type resolve payload has no uniform name key — and no name at all for five
types. `receipt_rows` groups by type, does ONE store read per type, and emits a full
`MentionRow` through the same `_row()` helper and `ICONS` table the search path uses, so a
receipt chip and a picker chip are byte-identically shaped. A missing entity still emits a row,
`stale: true` with an EMPTY label — absent means "no label available"; a fabricated name would
be a wrong claim about a name.

**5. Records de-duplicate on `(type, op, id)`, merging meta.** Two things need this. An
auto-assign over 40 envelopes followed by one correction must not read "41 envelopes edited".
And it lets a choke record an entity a SECOND time purely to attach provenance the first record
could not know — `match_review` does exactly that, stamping `verb=merge` onto a row the audit
choke already reported. Without the fold that would have been a double count; with it, it is
free.

**6. The chip head names the type.** Per-type groups (`[{type, count, ops}]`) sorted by count
then `descriptor.order`, at most two rendered plus "+N more". A group with several ops reads
"changed" rather than picking one. `disposal` and `envelope` both sat at order 25, so their
tie-break fell back to module import order — both are in scope, so `envelope` moved to 26 and a
test now forbids duplicate orders.

## Consequences

**A cascade legitimately produces several rows, and that is the design.** A vest writes a vest
event, an income transaction and a lot; each funnels through its OWN choke and reports itself,
so the chip shows three rows for one user action. The rule that keeps it honest rather than
doubled is: **each choke records ONLY the entity it is the choke for** — pinned per cascading
choke by test. The head reading noisier than the single action the user took is ACCEPTED for
this wave; if it grates, the fix is a `meta["caused_by"]` parent id and a nested render, not
suppressing rows.

**Checkpoint inclusion became a real bug surface.** `finalise()` drops the receipt entirely
when no NON-EXCLUDED file was snapshotted, so an in-scope domain whose store is checkpoint-
EXCLUDED would build a receipt and have it silently thrown away. A test now asserts every store
backing an in-scope type is absent from `_EXCLUDED_PATHS`, and that its own table covers every
recorded type so a new domain cannot skip the check.

**Two descriptor sources now read through the writer's own handles.** The `transaction`
descriptor's `rows`/`find_one` moved from a fresh `TransactionStore()` to
`services.ledger.transactions`, and `find_one` no longer filters deleted rows — a receipt
resolves a row the same turn just wrote, and `include_deleted=False` would have hidden exactly
the row a DELETE receipt has to name. Search is unaffected (it uses `search_override`).

**Planning reports from inside each `_op_*`, not from a wrapper.** The obvious shape — wrap the
dispatcher and record once — breaks `services/domain_introspect_ast`, which DERIVES each op's
field set from the dispatcher's source: a `change.get("month")` on the path every branch returns
through gets attributed to all 21 ops, and `describe` then reports fields they never read. This
was found by a failing test, not by inspection, and the per-op placement is the fix.

**Group ops record NOTHING, deliberately.** `group_create`/`rename`/`reorder`/`delete` touch
`category_groups` only — no envelope row is written — so a chip row for them would be a lie,
and there is no `group` mention type to render one with anyway. Same for moving an INCOME
category, which has no envelope at all.

**Deferred, not blocked.** Accounts (7+ direct call sites, no choke), payees, rules, categories'
own setters, watchlist, commitments, targets, instruments, artifacts, uploads: every one has a
stable id and is reachable — each needs a choke BUILT first. Accounts is the highest-value
deferred domain. `options` is additionally blocked on there being no `option` mention type, and
on `_apply_delete` returning a bare `{"ok": True}` that drops the id on its one destructive op.
`positions` is deferred because `add` writes three entity types and surfaces one id, so
recording it would double-count the auto-lot that already reports itself. Singleton documents
(profile, budgets, connector configs) are **permanently out** — there is no entity and never
will be; a `document` pseudo-type would render a chip that resolves to nothing.

**Only three types deep-link, on purpose.** `RECEIPT_ROUTES` carries `transaction`,
`account` and `commitment` — the three whose routes actually declare the search param in
`router.tsx::validateSearch`. Every other type renders a chip that does not navigate.
Wiring `/plan?envelope=<id>` would have looked complete and been worse: `/plan` has no
`validateSearch`, so the param is silently stripped and the user lands on Plan with nothing
selected after being promised a specific row. Adding a type is one line, AFTER its route
learns to address the entity.

**`MentionType` was 16 of 27 registered types.** Cosmetic while nothing constructed a chip of a
missing type; load-bearing the moment the receipt does. Widened as a prerequisite.

## Agent provenance

Built by the `harness/reliability-program` lane against an approved design. Decisions taken
mid-implementation and recorded above rather than left as apparent oversights: the per-op
placement in `planning.py` (forced by the introspection test), reading the transaction
descriptor through the ledger's own handle (forced by a monkeypatched-store test failure that
was really the "read through the writer's own handles" property asserting itself), and dropping
the `receipt_rows` internal de-dup once it was found to misalign the positional join with the
caller's list.
