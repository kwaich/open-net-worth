---
title: Money columns store repr(float)-exact decimal TEXT; service-layer arithmetic stays float, deliberately deferred
status: Accepted
date: 2026-08-08
deciders: Claude (implementer agent, storage plan 3 task 5 — revision 0008), repo owner (D5 ruling amendment, design spec addendum)
---

# 2026-08-08. Money storage becomes exact decimal TEXT; arithmetic stays float, a named future program

## Context

The design spec's original D5 line was "money stays as-is (float) in the first cut … migrating to
integer minor units or `NUMERIC` is a separate, later expand/contract migration." The owner's
2026-08-07 Addendum amended this for Plan 3 specifically: **"float money does NOT survive 'for
compatibility.'"** Plan 3 absorbs the money core byte-exact as floats first (task 3, revision 0007
— the round-trip verification a format change in the same step would make meaningless), then a
**separately-verified** revision within the same plan normalizes storage to a real decimal
representation (task 5, revision 0008).

Seven columns carry money: `transactions.{from_amount, to_amount, base_amount, fx_rate,
original_amount, quantity}` and `transaction_splits.amount`. This is the first revision in the
`db/alembic/versions/` package that alters a column's *type*, not just its rows — three things
followed from actually running it against seeded data, not from either dialect's documentation
alone:

1. A dialect-native `CAST` mid-`ALTER` does not reliably produce `repr()`-identical text (SQLite's
   REAL-affinity coercion and Postgres's `::text` cast each format their own way).
2. SQLite has no native `ALTER COLUMN … TYPE` — batch mode's only mechanism is DROP-and-recreate,
   and this connection runs with `PRAGMA foreign_keys=ON` (wired into every Alembic migration since
   task 4's own env.py hardening — see the sibling choke-seam ADR): the internal `DROP TABLE
   transactions` fires `ON DELETE CASCADE` on `transaction_splits`/`transaction_external_refs` as if
   it were an explicit delete, silently wiping both child tables the instant `transactions` itself
   is recreated. `PRAGMA foreign_keys=OFF` does not help — SQLite documents (and this task verified)
   that the pragma is a no-op while any transaction is open, and Alembic always runs the whole
   migration chain inside one.
3. Postgres requires an explicit `USING` cast expression for a `double precision -> text` type
   change; SQLite's batch-recreate ignores it (the copy step handles conversion instead).

## Decision

We will:

1. **Convert the seven money columns from `Float` to `Text`, holding `repr(float)` strings.**
   Python's float `repr` is the shortest decimal literal guaranteed to round-trip exactly back to
   the identical float (`float(repr(x)) == x`, guaranteed since Python 3.1's short-float-repr
   algorithm) — storage becomes exact and human-inspectable without introducing a new numeric type.
2. **Verification is snapshot-to-memory, then ALTER, then rewrite-and-compare — never trust the
   ALTER's own transient cast.** Every row's old values are read into memory BEFORE any DDL runs;
   after the ALTER, every promoted column is explicitly OVERWRITTEN with `repr(float(old_value))`
   computed from that in-memory snapshot, so the final stored text is provably `repr()`-exact
   regardless of what the dialect's own CAST happened to produce as an intermediate value.
   `_verify_upgrade_exact` then re-reads the (now Text) columns and checks, per row,
   `float(new_text) == old_float` EXACTLY, plus a **per-BOOK signed-sum invariant on
   `base_amount`**, `Decimal`-summed over `repr()` strings on both sides — never float-summed, so
   the check itself cannot hide a rounding drift. (Corrected from an initially-drafted per-*account*
   invariant during task 5's review: `base_amount` is stored as an unsigned magnitude, so a
   per-account signed sum is not a coherent invariant on this column — the per-book sum is.) Any
   mismatch raises, aborting the whole migration transaction; `ensure_migrated()`'s outer wrapper
   additionally restores the pre-migrate backup on failure. **Downgrade is symmetric** (fix round 1):
   it snapshots the canonical pre-downgrade TEXT values first and verifies `repr(new_float) ==
   old_text` exactly plus the same per-book Decimal-sum invariant in reverse, closing an initial
   asymmetry where `downgrade()` trusted the dialect-native cast with no verification at all.
3. **The SQLite cascade hazard is closed by dumping and restoring both child tables' complete rows
   via raw `SELECT *`** (bypassing SQLAlchemy's column-type value processing, so a JSON `payload`
   column round-trips as the exact bytes the driver returned) around the `transactions` ALTER —
   `transaction_external_refs` restores byte-identical (it carries no money column and has no other
   reason to be touched by this revision at all); `transaction_splits` restores with its old
   (still-float) `amount`, converted to text normally by the steps that follow. Postgres never drops
   the table, so this dance is skipped there entirely.
4. **Postgres gets plain `Text`, deliberately not `NUMERIC`.** `NUMERIC` would reintroduce
   dialect-casting edges and buys nothing while the arithmetic type stays float at the service
   boundary (below) — an exact-string column is dialect-symmetric either way.
5. **Service-layer arithmetic stays `float` — explicitly, permanently-for-now, deferred.** The
   store read boundary returns `float(text)`, bit-identical to a pre-migration float read; writes
   store `repr(float(value))`. This revision changes nothing about how money is COMPUTED, only how
   it is STORED. Converting the ~3,000 lines of monetary math across `services/` to `Decimal`
   arithmetic is its own future program, with its own correctness verification (this migration's
   per-row-exact + per-book-Decimal-sum pattern is a template for it, not a substitute) — bundling
   an arithmetic-semantics change into a storage-format change here would make THIS revision's
   round-trip verification meaningless, per the design spec's own stated principle.

**Verified at production scale (2026-08-08 dress rehearsal 3):** this is the load-bearing proof the
service boundary is genuinely invisible, not merely argued to be. The pre-cutover stack (float
JSON files, no revision 0008) and the post-cutover stack (Text-decimal DB, full chain through 0008)
computed `net_worth()`/`balances()`/`net_worth_history()` independently, from two different storage
representations of the identical real source data, and produced **byte-identical canonical-JSON
output** (sha256 match). Two independently-computed reductions agreeing exactly is strong evidence
against a subtle float/Decimal-boundary drift that a per-row unit test could miss. Full mechanism:
`.superpowers/sdd/2026-08-07-storage-plan3-money-core/task-8-report.md`.

## Consequences

### Positive

- Money is exact and human-inspectable in the database — no more "what did this float ACTUALLY
  round to" guessing when reading a row directly; `repr()`-exactness is proven per-row inside the
  migration itself, not assumed from Python's documentation.
- The storage-bug class this migration exists to close (silent precision loss on write/read) is
  eliminated, independent of whatever the arithmetic layer does with the value afterward.
- Two dialect-specific hazards (SQLite's cascade-on-recreate, Postgres's cast requirement) were
  found and fixed by actually running the migration on both dialects with real data shapes, not
  discovered later in production.
- The rehearsal's byte-identical parity result is now the standing regression proof that the
  float↔text boundary is lossless for every number the ledger's three headline read paths touch.

### Negative

- **Arithmetic is unchanged and still float.** This ADR does NOT fix float's known error classes
  (accumulated rounding drift across many operations, binary-fraction surprises on certain decimal
  literals) — a reader must not mistake "storage is exact" for "math is exact." The
  historical-rate-lock and ledger-math characterization tests are today's evidence the CURRENT
  boundary is invisible for real data; they say nothing about arithmetic correctness in the
  abstract.
- The deferred Decimal-math program has no committed scope, owner, or timeline as of this ADR — it
  is a named gap, not a scheduled one.
- A future engineer touching money arithmetic in `services/` who assumes storage exactness implies
  computation exactness will be wrong; this ADR is the correction for that assumption.

### Follow-ups

- The deferred-Decimal-math program itself: convert `services/` monetary arithmetic to `Decimal`,
  with a verification strategy derived from this revision's per-row-exact + per-book-Decimal-sum
  pattern. No ticket exists yet — flagging the absence here so it isn't mistaken for "already
  planned."
- Any future revision touching one of the seven money columns' TYPE again (not just their rows)
  should re-read this ADR's three dialect findings before assuming a plain `ALTER COLUMN` is safe.

## Alternatives considered

- **`NUMERIC` on Postgres** — rejected: reintroduces dialect-specific casting behavior for no
  benefit while the arithmetic type stays float; an exact string is dialect-symmetric and just as
  inspectable.
- **Converting service-layer arithmetic to `Decimal` in this same revision** — rejected: bundling a
  storage-format change with an arithmetic-semantics change would make the round-trip verification
  meaningless (the design spec's own explicit reasoning for absorbing byte-exact-as-float first,
  then normalizing separately) — this task keeps that same discipline one level deeper (storage
  format vs. arithmetic type, not just absorb vs. normalize).
- **Leaving money as `Float` permanently** — rejected: the owner's explicit "single user, no
  backward compatibility, industrial-grade schema" ruling overrides the original spec's
  "compatibility" framing; float-forever was never re-affirmed once that ruling landed.
- **A single interleaved read-then-write per row instead of snapshot-all/ALTER/rewrite-all** —
  rejected implicitly by the dialect hazards found: an ALTER that touches the table structure itself
  (SQLite's recreate) cannot be interleaved with a per-row rewrite that assumes the table already has
  its new shape; the two phases have to be strictly sequential with the snapshot captured before
  either.

## Agent provenance

- **Decided by:** Claude (implementer agent, storage plan 3 task 5 — revision 0008; downgrade
  symmetry added in that task's fix round 1). Recorded by the task 8 implementer agent, which
  independently verified the float↔text boundary's invisibility via the production-scale rehearsal
  cited above. Lane/branch: `lane/storage-plan1` (worktree `storage-plan1`).
- **Alternatives weighed:** `NUMERIC` was rejected for dialect divergence with no arithmetic
  benefit; converting arithmetic in the same revision was rejected against the design spec's own
  byte-exact-then-normalize discipline; permanent float storage was rejected against the owner's
  no-compatibility ruling.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
  (D5, and the Addendum's "D5 upgraded" ruling); `backend/db/alembic/versions/0008_money_decimal.py`
  (the revision's own docstring carries the full dialect-hazard writeup this ADR summarizes);
  storage-plan3 task 5 report and its fix-round-1 package
  (`.superpowers/sdd/2026-08-07-storage-plan3-money-core/task-5-report.md`); this task's rehearsal 3
  report (`.superpowers/sdd/2026-08-07-storage-plan3-money-core/task-8-report.md`).
- **Why this way (one line):** storage can be made exact today with a self-contained, doubly-verified
  migration; making the ARITHMETIC exact is a materially larger, separately-scoped program that a
  storage migration must not smuggle in unverified.

