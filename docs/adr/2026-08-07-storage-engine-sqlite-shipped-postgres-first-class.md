---
title: SQLite (WAL) ships as the default engine; Postgres is a first-class, CI-tested second dialect from the first Alembic revision
status: Accepted
date: 2026-08-07
deciders: Claude (implementer agent, storage plan 1) + owner ratification (spec 2026-08-05, Decisions D1/D10)
---

# SQLite (WAL) ships as the default engine; Postgres is a first-class, CI-tested second dialect from the first Alembic revision

## Context

The whole-file JSON read-modify-write model failed in the field: `evidence.json` measured 108.4
MB / 83,381 rows on 2026-08-05, appending one row serialized/fsynced/replaced the whole file, and
the checkpoint hook gzipped those same 108 MB first. The live data dir carried ~1.1 GB of orphaned
`evidence.json.tmp-*` files from interrupted atomic writes. This superseded
`docs/launch/PLATFORM_MASTER_PLAN.md` P1-A's SQLite-only ruling and closed its Open Question #1
("SQLite-everything vs SQLite-for-ledger + append-only logs").

The owner's product context (spec 2026-08-05) names three deployment modes in order: self-hosted
single user (today), family/sharing (one self-hosted install, several people), and hosted SaaS (a
real product for strangers). This overturns the implicit single-user-forever assumption P1-A was
written under — but does not by itself overturn SQLite: at 1,806 transactions the money core is
four orders of magnitude from where SQLite strains, and `cp`-to-backup / zero install dependency
is the self-hoster value proposition P1-D's operator-trust kit is built on. Hosted mode, however,
is confirmed and near enough that discovering dialect assumptions after 40 tables hold production
data would be expensive to discover late.

An engine decision was needed that serves BOTH: ship something a self-hoster can `cp` to back up
today, without closing the door on a real multi-tenant Postgres deployment later.

## Decision

We will ship **SQLite in WAL mode** as the default engine (`backend/db/engine.py`; `PRAGMA
journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL` set on every
connection) and make **Postgres a first-class, CI-tested second dialect starting at the very first
Alembic revision** (`0001`) — not a later port project.

The one rule that makes one schema serve both dialects, applied everywhere in `backend/db/`:

> No dialect-specific SQL, and never index inside a JSON column. If a field needs to be queried,
> promote it to a real column.

Concretely, in this plan's stage-1 cutover (`evidence`, Alembic revision `0002`):

- `evidence.payload` is a generic SQLAlchemy `JSON` column — on Postgres this compiles to `json`,
  **deliberately not `jsonb`**. `payload` is the verbatim source of truth the round-trip export
  reads back for the cutover's exact-equality verification; `jsonb` normalizes key order on
  storage, which would silently defeat that verification. Anything actually queried (`expires_at`,
  `domain`, and the `subjects` fan-out) is promoted to a real column/child table instead of being
  reached for inside the JSON — `evidence_subjects` exists for exactly this reason.
- `render_as_batch` is enabled only for the SQLite connection in `backend/db/alembic/env.py`
  (SQLite's `ALTER TABLE` limitations require rebuild-and-copy; Postgres does not need it) — the
  one dialect-conditional in the whole migration path, and it is about *how* a migration runs, not
  what schema either dialect ends up with.
- CI's `db-postgres` job (`.github/workflows/ci.yml`) runs the **entire** `tests/db` suite a
  second time against a real `postgres:16` service container
  (`ONW_TEST_DATABASE_URL=postgresql+psycopg://...`); the same 31 tests run against SQLite for
  free as part of the main `test` job's default `sqlite:///` URL. As of task 9: **Postgres 31/31 +
  SQLite 31/31, zero dialect leaks.**

**No FTS in v1** (D10): full-text search is the one irreducible dialect divergence (SQLite FTS5 vs
Postgres `tsvector`/`tsquery` — genuinely different mechanisms, not something the "no
dialect-specific SQL" rule can paper over). It is deferred entirely, to be implemented later behind
a single repository method with a dialect-specific body on each side, rather than let a
`LIKE '%...%'` or a one-off FTS5 virtual table creep into a store now.

## Consequences

### Positive
- Self-hosters keep the entire value proposition this design exists to protect: one file, `cp` to
  back up, zero install dependency, unchanged from before this migration.
- Hosted mode has zero surprise dialect debt to discover later — every schema change from
  revision `0001` onward is proven against real Postgres in CI, in the same pull request that
  introduced it, not during a future "port to Postgres" project against 40 tables of live money
  data.
- The discipline rule is cheap to hold (one sentence, one code-review question: "does this touch
  a JSON column's insides, or use dialect syntax?") and per the spec removes ~90% of
  SQLite/Postgres divergence on its own — `ON CONFLICT` upserts, `RETURNING`, partial indexes,
  CTEs and window functions all work unmodified on both.
- The `json`-not-`jsonb` call is a concrete, already-proven instance of the rule paying for
  itself: the round-trip verification that gates every cutover (spec D6 step 4) would have false
  positives on key-order normalization if payload columns were `jsonb`.

### Negative
- CI cost: a Postgres service container on every relevant push (~2 minutes per the spec's own
  estimate) — accepted as cheap relative to the cost it avoids (discovering an accumulated dialect
  assumption once 40 tables hold production data).
- The two dialects' backup primitives are NOT symmetric, and this design does not paper over that:
  SQLite gets a fully automatic verified backup (`sqlite3`'s native backup API, consistent under
  WAL) inside `db/migrate.py`; Postgres in Plan 1 has no automatic equivalent and requires an
  operator-supplied `pg_dump` plus an explicit `ONW_MIGRATE_ASSUME_BACKUP=1` opt-in before it will
  even attempt a pending migration (see the companion fail-closed-migrations ADR). This is
  acceptable while Postgres is CI-only, and is recorded there as a gap to close before Postgres is
  offered as a real deployment target.
- The discipline rule is enforced by code review and the CI Postgres job catching a violation
  after the fact, not by a static linter today — a dialect-specific expression that happens to
  also be valid (if slower or semantically different) SQL on both engines would not necessarily
  fail CI.

### Follow-ups
- FTS stays explicitly deferred (D10) until a real requirement exists; land it behind one
  repository method with two dialect bodies when it does, never a schema-visible divergence.
- Watch for future "just index into the JSON column" requests as query needs grow on Tier-2
  stores — the answer is always "promote the field to a real column," not an exception to the
  rule.
- Before Postgres becomes a supported PRODUCTION target (as opposed to CI-only), it needs its own
  automatic verified-backup story symmetric to SQLite's.

## Alternatives considered

- **Postgres-only** — rejected: couples an urgent substrate fix (the 108 MB whole-file rewrite
  problem) to a new operational dependency for every self-hoster, destroys the `cp`-to-backup
  story P1-D's operator-trust kit assumes, and stalls the active ① Foundation hardening loop on
  unrelated ops work.
- **SQLite-only, port to Postgres later when hosting is actually built** — rejected: the port
  itself is not the expensive part; the *undetected dialect assumptions accumulated in the
  meantime*, across every table added between now and that future port, are. A ~2-minute CI job
  starting today is cheap insurance against that.
- **`jsonb` for the `evidence.payload` column** — rejected: `jsonb` normalizes key order on
  storage, which is fine for a column something queries into but wrong for a column whose whole
  job is being the verbatim source of truth a round-trip export must reproduce exactly (spec D6
  step 4's abort-on-any-diff verification). Decided during task 6's review and reasserted
  deliberately by task 9 rather than silently inherited.
- **A portability ORM/query-builder layer to abstract dialect differences further** — out of
  scope for this decision (that is D2's access-layer choice, SQLAlchemy Core, recorded separately)
  — this ADR is the engine ruling, not the access-layer ruling.

## Agent provenance

- **Decided by:** Claude (implementer agent), storage plan 1 (`lane/storage-plan1` worktree),
  ratifying spec decisions D1/D10 into a formal ADR at task 10 once both dialects had real,
  passing evidence behind them.
- **Alternatives weighed:** see above — Postgres-only and SQLite-only-then-port were the spec's
  own named rejections; the `json`-vs-`jsonb` call was made live during implementation (task 6
  review, reasserted task 9) and is folded in here as the rule's first real application.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
  (D1, D10, the risk table); `.github/workflows/ci.yml`'s `db-postgres` job;
  `backend/db/tables.py`'s `evidence.payload` column + comment; task 9's completion note
  ("Postgres 31/31 + SQLite 31/31, zero dialect leaks"); task 10's live-data dress rehearsal
  (86,145 real evidence rows migrated in 3.4s against a clone of the production file).
- **Why this way (one line):** ship the thing that preserves today's self-hoster promise
  unchanged, while paying a small, constant CI cost to make tomorrow's hosted path provably safe
  from day one instead of discovered-broken later.
