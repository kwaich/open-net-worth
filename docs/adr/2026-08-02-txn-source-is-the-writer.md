---
title: txn.source names the WRITER — one fact, one field
status: Accepted
date: 2026-08-02
deciders: Claude (senior-engineer lane) + owner
---

## Context

`txn.source` answered two unrelated questions at once. Measured on a live production book:

```
txn.source              1116 ai · 248 agent · 127 csv_import · 31 manual · 12 pdf_import · 7 email
external_refs[].source   273 email · 200 xlsx · 168 pdf
```

`ai`/`agent`/`manual` name **who wrote the row**; `csv_import`/`pdf_import` name **what format
arrived**. They vary independently — the same principal arrives both ways (`ai`: 937 bulk / 179
single; `agent`: 59 bulk / 185 single) — so one field could not hold both, and the one it
destroyed is the one nothing else records: **139 rows had no recoverable writer** and rendered
"Added by: File import".

The format was never lost. `external_refs[].source` records it correctly for **343** rows versus
`source`'s 139, and survives a merge (a merge appends a ref; it never rewrites the writer).

Four concrete defects followed:

1. **The format copy was wrong.** `importer.py` stamped `csv_import` for **xlsx**;
   `decisions.py` expressed the same rule differently, so `qif` became `qif_import` through one
   site and `csv_import` through the other.
2. **The vocabulary rotted silently.** Every ingest path had to be remembered in four places
   (`KNOWN_SOURCES`, `DELIBERATE_SOURCES`, `REVIEW_ON_CREATE_SOURCES`, `_DISPLAY_BY_SOURCE`).
   Three live writers minted values `KNOWN_SOURCES` lacked — `pdf_import`, `recurring`,
   `import`. `recurring` was in `REVIEW_ON_CREATE_SOURCES` while absent from `KNOWN_SOURCES`.
3. **Unknown failed OPEN, in the worst direction.** `default_needs_review("acme_bank")` →
   `False` (skipped review entirely) and `display_source` → `"manual"` (**rendered as "You"**).
   A third-party plugin was trusted *more* than the owner's own CSV import, and its rows claimed
   the owner wrote them. `pdf_import`'s **12 rows bypassed the review queue** this way.
4. **`api/ledger.py` erased unrecognised sources**, rewriting them to `"api"` — so a
   user-installed integration could not record its own identity, contradicting ADR
   `2026-07-09-extensibility-substrate`.

## Decision

**`source` answers exactly one question: what kind of writer.** Seven values, closed:

```
manual · ai · agent · automation · import · api · feed
```

It does **not** name *which* provider. A Plaid row is `feed`, and `plaid` lives in
`external_refs`. **This is what lets the set stay closed**: a new provider needs no value here,
so a user-installed integration works on day one with no core change.

- **`feed`, not `connector`** — `backend/connectors/` already means *chat* connectors, and bank
  adapters live in `services/ingestion/`. `feed` is already the display bucket ("Bank feed") and
  the old set name, and unlike `sync_connector` it does not bake in a pull direction a webhook
  would violate.
- **`import` is its own writer**, not folded into `api`: a file the owner supplied is genuinely
  distinct from an unidentified HTTP call.

**Retired values keep parsing.** `canonical_writer()` maps them read-side — a source string is
persisted data (stored rows, backups, in-flight payloads, third-party callers). The migration is
therefore **optional**: a migrated row and an un-migrated one are indistinguishable to every
reader.

**Two fail-safe flips:**

- `TRUSTED_WRITERS = {manual, feed}`; everything else — **including unknown** — is reviewed.
  Stated as the exception list so a writer nobody thought about fails safe.
- An unknown writer renders "File import", never "You".

**`api/ledger.py` stops rewriting.** A *retired alias* is canonicalised (a rename); an *unknown*
value is kept. Those are different operations and only the first is legitimate.

**Merges carry the absorbed row's batch id** when the canonical has none — 63 rows carried a
statement ref with no batch id, which is what made a batch-scoped check compare a statement's
full swing against only its 2 newly-created rows.

## Consequences

**Positive.** The vocabulary stops growing with every format or scheduler. Three unregistered
writers become impossible. "Added by" is answerable for every row. Unknown fails safe.

**Negative.**
- Historical rows that read "File import" will read "Lucius"/"Agent"/"You" after migration.
- `_is_deliberate` had to canonicalise before testing membership — a chat channel IS the owner
  typing. Testing the raw string made the answer depend on which surface a row arrived through.
- One test contract changed: a caller sending `csv_import` now stores `import`.

**Not a risk: dedup.** `txn.source` is not a matching input. `_classify` reads nine fields plus
`description`, `original_amount/currency` and `external_refs`; the L0 floor keys on
`(external_refs.source, ref)` alone. Only `external_refs[].source` matters to matching, untouched.

## Alternatives considered

- **An OPEN connector-id set** (`source: "plaid"`, validated against an installed-connector
  registry). Rejected: duplicates `external_refs` and needs a registry lookup to validate what
  the row already states. The closed set gets the same extensibility for free.
- **Deleting `DELIBERATE_SOURCES`, using `batch_id is None`.** Wrong — an agent logging one row
  at 3am also has no batch id but was unattended. A claimed bug ("937 bulk `ai` rows are wrongly
  deliberate") was also wrong: those rows carry batch ids, so `_same_feed` exempts them first.
- **A generic `statement_import` value.** Keeps a mechanism-shaped value in a writer-shaped field.
- **Adding the three missing names to `KNOWN_SOURCES`.** The five-minute fix; patches one more
  name into an already-inconsistent roster and leaves 139 rows unattributable.
- **Dropping `source` entirely (Actual Budget / YNAB both do).** Rejected: neither has an AI
  writing to the ledger. With Lucius, agents and connectors writing autonomously, the writer is
  the one thing a user cannot reconstruct from anywhere else.

## Agent provenance

- **Decided by:** Claude (senior-engineer lane), owner driving during live debugging.
- **Driving artifacts:** RFC `.claude/rfcs/2026-08-02-transaction-provenance-one-fact-one-field.md`;
  owner's question "why do we have csv_import vs pdf_import?"; measured live distribution;
  comparison against Actual Budget, YNAB, Firefly III, Wealthfolio.
- **Why this way:** the field's two questions vary independently, so one field must lose one —
  and the one it currently loses is the one nothing else records. Firefly III is the cautionary
  case: three identity fields, one (`import_hash_v2`) documented as unused in dedup.
