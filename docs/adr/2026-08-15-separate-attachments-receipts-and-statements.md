---
title: Store chat attachments, transaction receipts, and statements separately
status: Accepted
date: 2026-08-15
deciders: orchestrating Codex agent + repo owner
---

# Store chat attachments, transaction receipts, and statements separately

## Context

The generic chat upload endpoint currently writes every supported upload to `UploadStore` and
`StatementStore`. Model-ready image or text content is then also copied into the conversation
transcript. Telegram photos follow a different path: they bypass `UploadStore` and survive only as
inline image blocks in the transcript. Transactions have no first-class receipt collection.

This conflates three independently useful product concepts:

- A **chat attachment** is a durable file supplied through any conversation interface so Lucius can
  use it as external context. It may be a screenshot, receipt, contract, statement, spreadsheet, or
  any other supported artifact. Uploading it makes no claim about its financial meaning.
- A **transaction receipt** is durable supporting evidence in transaction history. A transaction can
  have a list of receipts. A receipt is created only when the user asks to preserve/attach it as a
  receipt, or when the content is unambiguously a receipt and the transaction flow uses it as such.
- A **statement** is a financial document explicitly imported into the statement workflow, with
  parsing, account selection, reconciliation, review, batches, and statement history.

These concepts require different retention, deletion, audit, and discovery behavior. Sharing one
record and merely assigning multiple roles would couple their lifecycles: deleting chat context could
erase transaction evidence, retaining a receipt could make it appear in the upload catalog forever,
and every arbitrary upload could continue leaking into statement history.

File format cannot determine the destination. A receipt, screenshot, and scanned statement may all
be images. Ambiguous content remains a chat attachment only. Promotion is an explicit copy into a
different domain store, not a mutation or relabelling of the attachment record.

Raw attachment bytes also cannot be copied into every future model prompt. The conversation retains
a stable attachment reference; the current turn resolves newly supplied external context, and later
turns or tools retrieve it on demand.

## Decision

We will use three separate stores, identity namespaces, and lifecycle boundaries.

### Chat attachments

1. Every supported file uploaded through web, Telegram, or another chat adapter is persisted once in
   `AttachmentStore` before the turn starts.
2. Conversation messages persist stable attachment ids. The newly uploaded artifacts are resolved as
   external context for the current turn; their raw bytes are not embedded permanently in every
   subsequent prompt or used as the durable transcript representation.
3. An attachment remains an attachment unless a receipt or statement creation boundary explicitly
   copies it elsewhere. Deleting chat context affects only `AttachmentStore` after any dependent
   promotion has completed.

### Transaction receipts

4. `ReceiptStore` owns receipt metadata and bytes independently of `AttachmentStore` and
   `StatementStore`. Receipt ids are not attachment ids or statement ids.
5. A transaction supports zero or more receipt ids through an explicit transaction-receipt
   relationship. One receipt may support multiple transactions when one artifact documents several
   ledger entries.
6. A receipt is created from a chat attachment only when:
   - the user explicitly asks to save or attach it as a receipt; or
   - the image is unambiguously a receipt and Lucius uses it to create or identify the supported
     transaction.
   Ambiguous images remain attachments and require user intent before promotion.
7. Promotion copies the original artifact and required metadata into `ReceiptStore`, records
   `source_attachment_id` as provenance, then links the receipt to the transaction through the ledger
   write choke. Deleting the source chat attachment does not delete the receipt.
8. Attaching, unlinking, and deleting receipts are explicit audited operations. Receipt deletion must
   surface existing transaction links and must never silently remove transaction evidence.

### Statements

9. `StatementStore` owns statement files and metadata independently. A generic chat upload never
   creates a statement record.
10. A statement is created only through explicit statement import or promotion. Promotion copies the
    source artifact into `StatementStore`, records `source_attachment_id`, and is idempotent.
11. Statement tools and statement-history UI read only `StatementStore`; receipt and attachment
    catalogs never appear in `list_statements`.

### Migration

12. Existing records cannot be classified from MIME type alone. Legacy statement rows require
    positive statement evidence such as parsed statement facts, account assignment, import batches,
    or an owner decision. Ambiguous rows remain preserved until reviewed.

## Consequences

### Positive

- Chat context, transaction evidence, and statement history can evolve and be deleted independently.
- Every conversation interface gets the same durable attachment behavior.
- Receipts remain discoverable from the transactions they support even after the originating chat or
  attachment is removed.
- Arbitrary screenshots and documents no longer pollute statement history.
- Explicit source lineage shows which chat artifact produced a receipt or statement without sharing
  its identity or lifecycle.
- Model context remains bounded because stored artifacts are referenced and resolved on demand.

### Negative

- Promotion deliberately duplicates bytes across lifecycle boundaries. That storage cost buys
  independent retention and deletion semantics.
- A new receipt store and transaction-receipt relationship require schema, service, tool, API, and UI
  work.
- Cross-channel upload paths must converge on `AttachmentStore`; Telegram's direct-photo path needs
  migration.
- Confidence criteria for "unambiguously a receipt" need a fail-closed contract and regression
  fixtures. False promotion creates unwanted transaction-history evidence; uncertainty must leave the
  artifact attachment-only.
- Legacy data needs conservative review and may contain unavoidable ambiguity.

### Follow-ups

- Replace `UploadStore` with or migrate it into the explicit `AttachmentStore` contract, keeping
  stable ids and existing user data where possible.
- Stop storing provider-expanded base64/text attachment payloads as the durable conversation shape;
  persist attachment-reference blocks and resolve them into a bounded provider projection.
- Route Telegram photos/albums and every future chat interface through the same attachment write
  boundary.
- Add `ReceiptStore`, the transaction-receipt relationship, audited attach/unlink/delete operations,
  and transaction-history/detail presentation.
- Define and test the fail-closed clear-receipt promotion rule; preserve source attachment provenance
  and automatically link receipts when Lucius books transactions from them.
- Remove unconditional `StatementStore.create()` calls from generic web and Telegram upload paths;
  add explicit, idempotent statement promotion with source provenance.
- Define separate backup/export/deletion behavior for all three stores before migrating legacy data.
- Bound and filter `list_statements`, expose deterministic card-statement status to Lucius, and fix
  expanded tool-result ownership. These remain separate board items.

## Alternatives considered

- Use one artifact store with attachment/receipt/statement role links — rejected because the owner
  requires independent storage and lifecycles; one record makes deletion and retention interdependent.
- Treat a receipt as only a link from a transaction to the original chat attachment — rejected
  because deleting conversation context would delete or break durable transaction evidence.
- Move an attachment record into the receipt or statement store — rejected because the conversation
  would lose its source context and the operation would not preserve independent provenance.
- Keep dual-writing every chat upload to statements — rejected because an upload is not evidence of
  statement intent.
- Auto-promote every image that resembles a document — rejected because ambiguous screenshots must
  remain external context until user intent or clear receipt evidence exists.

## Agent provenance

- **Decided by:** orchestrating Codex agent + repo owner
- **Lane / branch:** `main` in `/Users/owner/code/my-net-worth`
- **Alternatives weighed:** one store with roles, transaction links to chat attachments, move semantics, unconditional statement dual-write, and broad automatic classification
- **Driving artifacts:** owner conversation `be8e87c2-9fc1-4528-ac92-abb1708df64b`, the expanded `list_statements` result, and the 2026-08-15 storage-boundary corrections on `docs/launch/BOARD.md`
- **Why this way (one line):** conversation context, transaction evidence, and statement imports are independent products and therefore need independent durable storage and explicit provenance-preserving promotion.

## Amendment (2026-08-16)

Item 6's "fail-closed clear-receipt promotion rule" has no code-level ambiguity check — a
code review of the Phase 4 implementation (`backend/services/receipts.py`,
`backend/ai/tools/receipts.py`) correctly flagged that the ADR text could be read as
promising mechanical enforcement that does not exist. Recorded here plainly so the reader
never assumes a stronger mechanism than what actually runs:

`attach_receipt` promotes unconditionally whenever it is called with `attachment_id` +
`transaction_id` — there is no server-side check for "did the user explicitly ask" or "is
this image unambiguously a receipt". Enforcement is the SAME two-layer mechanism every
other write tool in this codebase relies on (CLAUDE.md's security section, "no
unconfigurable floor anywhere"):

1. The tool description instructs the MODEL's judgment — call it only on explicit user ask
   or unambiguous receipt-and-in-use, leave anything uncertain as a plain attachment.
2. The standard `tool_permissions` confirm gate (`needs_approval` by default,
   configurable to `always_allow`/`blocked` like any other write tool) is the backstop.

This is not a deviation from how this codebase treats every other write tool — a trade,
a payment, a category rule all rely on the identical two layers, by owner decision
(`2026-08-13-mcp-permission-config-is-the-single-authority`). Building heuristic
image-ambiguity detection to enforce item 6 mechanically was considered and rejected as
out of scope and risky to get right (false negatives silently promote; false positives
silently block a wanted receipt) — a follow-up, not a Phase 4 requirement. Item 6's prose
and the "Define and test the fail-closed clear-receipt promotion rule" follow-up should be
read as describing the MODEL's decision rule, not a code-enforced gate.

## Amendment (2026-08-21) — receipts are checkpoint-INCLUDED, reversing item 4's "Both checkpoint-EXCLUDED" line

The owner has reversed the checkpoint-exclusion call this ADR's Decision text stated in
passing ("Both checkpoint-EXCLUDED... and portable/BRIDGED... identically to
`attachments`/`artifacts`") — `receipts`/`transaction_receipts` are now
checkpoint-INCLUDED, undoable via `/rewind` like every other Lucius write choke.
Portability is unchanged (still portable/BRIDGED, still not secret) — checkpoint
inclusion and portability are independent axes.

The full reasoning for the reversal lives on the amendment to
`docs/adr/2026-08-16-receipt-store-and-explicit-statement-promotion.md`, the ADR that
actually made this specific call (its Decision 2 and "Alternatives considered" — rejecting
checkpoint-inclusion for the identical reasoning now reversed). Read that amendment for
the row-level-checkpointing argument; this note exists only so a reader of THIS ADR's
Decision text isn't left with a claim the code no longer matches.
