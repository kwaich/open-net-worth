---
title: One identity namespace for a statement file
status: Accepted
date: 2026-08-04
deciders: Claude (senior-engineer lane) + owner
---

## Context

`external_refs[].source` conflated two axes: *is this a statement file* and *is the ref native*.
Six format namespaces existed — `csv`, `qif`, `xlsx`, `pdf`, `mt940`, `camt` — plus `ofx`.

**csv/qif/xlsx/pdf all mint through the same `importer.external_ref()`.** Identical row content
in two of those formats therefore produces the **identical ref string** — but the differing
namespace made the pair a MISS at the L0 floor, which keys on the `(source, ref)` pair. So
re-importing last month's statement as xlsx instead of pdf **double-booked instead of
no-opping**.

A second, latent hazard: `is_authoritative_source` was an exclusion list, so anything not named
was authoritative. Shrinking that list would make its fail-open default *more* load-bearing —
and `statement`, the namespace this change introduces, already returned `True` before it existed.

## Decision

**Six namespaces collapse to one: `statement`.** `ofx` stays its own.

```
csv · qif · xlsx · pdf · mt940 · camt   →   statement
ofx                                     →   ofx  (unchanged)
```

**Refs are byte-identical before and after** — only the `source` component moves.

- **csv/qif/xlsx/pdf** collapse because they share one minting function; this is the only part
  that buys dedup, and it is exactly what turns an L0 miss into an L0 hit.
- **mt940/camt** collapse for free — their `<acct>:<x>` refs can never string-equal an
  `external_ref()` hash, so no L0 hit is unlocked. They are included so no residual per-format
  namespace survives to re-create the conflation. Zero live refs on the owner's book.
- **`ofx` stays out.** A FITID is spec-mandated unique per account (OFX 2.x §3.2.1), so it is
  genuinely authoritative. Collapsing it would *downgrade* a native id, unlock nothing, and
  silently flip `_maybe_adopt_settled_amounts._is_feed_grade` for a hand-supplied OFX pair.

**Companion (shipped as its own commit): `is_authoritative_source` fails CLOSED.** It is now an
allowlist — `AUTHORITATIVE_ID_NAMESPACES` — and anything unclassified is non-authoritative. The
error directions are not symmetric:

| direction | consequence |
|---|---|
| false negative — a real feed read as non-authoritative | F03 does not veto → the pair is **surfaced for review**. Cost: noise. |
| false positive — a content hash read as native | F03 **vetoes** → a second row is written with no human in the loop. Cost: a silent double-count in net worth. |

Under the old default, a retired namespace re-entering the book (checkpoint revert, restored
backup, old export re-imported, **an unmigrated merge journal**) was authoritative and therefore
dangerous. Under the allowlist it is inert.

## Consequences

**Positive.** A statement re-imported in a different export format now hard-dedups. One
namespace instead of six. A stale ref is inert rather than hazardous.

**Negative / discovered.**
- **The migration must rewrite `merge_journal.json`, not just the ledger.** `ledger.unmerge`
  restores `canonical_before` via `transactions.upsert` from a deep-copied snapshot carrying
  `external_refs`, and the journal is not a checkpoint companion — so an unmigrated journal
  **resurrects a retired namespace into the live ledger on the next undo**.
- The fail-closed flip produced exactly the predicted noise: two tests had invented namespaces
  (`fake`, `manual:N`) reading as authoritative and vetoing genuine matches *for the wrong
  reason*. Both fixed at the root.
- 12 tests asserted the per-format namespace; all updated.

**A real defect the migration surfaced and refused to paper over.** Two live rows share the ref
`6b6de7a9ada3f876f7e05f8d` — identical S$12.90 SaaS-vendor charges from one statement, where the
intra-file disambiguation wrote **both** the suffixed ref (`…:1`) and the un-suffixed one onto
the second row. `by_external_ref` is therefore *already* order-dependent for that ref. The
collapse neither causes nor worsens it, so the script **reports it and proceeds** — refusing
would block a safe migration over a pre-existing condition it does not touch, and silently
rewriting it would hide a real defect.

## Alternatives considered

- **Derive nativeness from the ref SHAPE instead of the namespace.** Rejected — this was the
  original brief and it is wrong twice: no shape is self-classifying (`import_mt940` mints
  `<acct>:<24hex>` *fabricated* and `import_ofx` mints `<acct>:<FITID>` *native* — same shape),
  and a bank's printed Ref No. is not stable across exports, so making `ref:` authoritative
  would have F03 **veto** the cross-format duplicate — making the target bug worse.
- **Collapse `ofx` too.** Downgrades a genuinely native id for no dedup gain.
- **Keep the exclusion list.** Leaves the fail-open default load-bearing at exactly the moment
  the list shrinks to one entry.

## Agent provenance

- **Decided by:** Claude (senior-engineer lane), owner directing.
- **Driving artifacts:** RFC `.claude/rfcs/2026-08-01-one-statement-identity-namespace.md`
  (which corrected the original brief); measured live book — 475 refs (307 xlsx, 168 pdf),
  1 pre-existing ambiguous pair, 0 new collisions.
- **Why this way:** the collapse is the only change that converts an L0 miss into an L0 hit,
  and the allowlist is what stops the shrunken exclusion set from becoming a hazard.
