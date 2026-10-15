---
title: Identity (users/books/book_members) is seeded 1/1/1 with deterministic sentinel ids now; `book_id` lands on Tier-1 (money) tables only, as they arrive
status: Accepted
date: 2026-08-07
deciders: Claude (implementer agent, storage plan 1) + owner ratification (spec 2026-08-05, Decisions D4/D5)
---

# Identity (users/books/book_members) is seeded 1/1/1 with deterministic sentinel ids now; `book_id` lands on Tier-1 (money) tables only, as they arrive

## Context

Spec 2026-08-05 names three deployment modes the same schema must eventually serve: self-hosted
solo (today — 1 user, 1 book, 1 owner membership), family/sharing (several people, one shared
book, per-user roles), and hosted SaaS (many users, many books, invite-based membership). All
three are the *same* shape, differing only in row counts and in the auth surface layered above
`db/` — which is why the spec calls for landing the identity tables now rather than when family
mode is actually designed.

The tiering decision (D5) splits stores into four tiers: **Tier 1** relational, typed, `book_id`
(the money core — accounts, transactions, positions, lots, …); **Tier 2** row-with-JSON-payload,
world/reference data and append-only logs, explicitly **no** `book_id` (evidence, price history,
cost logs — identical for every user and every book); **Tier 3** settings/config, no `book_id`;
**Tier 4** files stay files. `book_id` is a Tier-1-only concept by design, not something every
table gets.

This plan's stage-1 cutover only ports `evidence` — Tier 2, world data, explicitly zero money
risk, chosen first for exactly that reason. **No Tier-1 (money) table exists in the database yet**
— `accounts.json`, `transactions.json`, and the rest of the money core are still plain JSON files
with no `book_id` concept anywhere. So this decision is deliberately split in time: land the
identity model and the partitioning RULE now, while the column itself only starts appearing on
real tables once Plan 3 ports the money core.

## Decision

We will create `users`, `books`, and `book_members` in Alembic revision `0001` — the very first
revision, ahead of any store cutover — and seed each with **exactly one row**:

```
users:        usr_000000000000  (email="", display_name="Owner")
books:        book_000000000000 (name="My Book", base_currency="")
book_members: (usr_000000000000, book_000000000000, role="owner")
```

The seeded ids are **deterministic sentinels**, not randomly generated — chosen so that QA clones,
fixtures, and the round-trip diff this whole plan is built on stay reproducible across
installs and across repeated migration runs, instead of a fresh `uuid4()` making revision `0001`
non-deterministic. Hosted mode mints ordinary random ids for every NEW row created after this
point; those can never collide with the all-zeros sentinel.

`book_id` will be added to **Tier-1 (money) tables only**, as each is cut over from JSON to the
database in later plans (Plan 3 for the money core, Plan 4+ for planning/investments) — never to
`evidence` or any other Tier-2/3 store, which stay world/instance data with no tenant concept at
all. Every Tier-1 index will lead with `book_id` (`(book_id, date)`, not `(date)`). Enforcement —
a repository base class injecting `WHERE book_id = ?` so no Tier-1 query can omit it, reinforced by
Postgres Row-Level Security — is spec'd but is Plan 3 scope, not this task's: it has nothing to
enforce yet, since no Tier-1 table carries the column today.

## Consequences

### Positive
- Every Tier-1 table born from Plan 3 onward gets `book_id` and a leading-`book_id` index from its
  own creation — never a retrofit across live money data.
- Deterministic sentinel ids make cross-install artifacts (QA clones, fixtures, the round-trip
  export/import used for every cutover's verification) comparable and stable, which the plan's
  entire safety mechanism (exact-equality diff, never a spot check) depends on.
- Zero user-visible change today: one user, one book, one owner membership is exactly today's
  single-password, single-install reality — restated in the schema, not altered by it.
- The identity/auth surface question is cleanly separated from the schema question:
  `auth_store.py` stays singular (one password, one session, one API token) unchanged; adding
  per-user login is new work layered above `db/`, not a migration of what's below it.

### Negative
- This carries `users`/`books`/`book_members` — three tables, a role column nothing reads yet, and
  a `book_id`-partitioning convention — for a feature (family sharing / hosted SaaS) that may
  never ship. The spec names this explicitly as accepted YAGNI risk: carrying it costs one column
  and one base-class filter per Tier-1 table; retrofitting later costs a migration across the
  *whole* ledger plus opens a cross-tenant-data-leak bug class that carrying the column from day
  one closes structurally.
- **The partition key protects nothing yet.** As of this ADR, every real money table
  (`accounts.json`, `transactions.json`, positions, lots, …) is still a plain JSON file with no
  `book_id` and no tenant concept whatsoever. This decision records the identity model and the
  rule; it is not a completed retrofit, and must not be read as one.
- `book_members.role` (owner | member | viewer) exists in the schema with no code path that reads
  it — today's single auth surface is unaware these tables exist.
- The partition asymmetry the spec flags — ledger data is `book_id`-scoped (shared) while chat
  history and Lucius's memory would be `user_id`-scoped (private) in family mode — is recorded in
  the spec but deliberately unresolved here; it is a family-mode design question, not a storage
  substrate one.

### Follow-ups
- Plan 3 (money-core cutover) adds `book_id` to every Tier-1 table it ports, plus the
  repository-base-class `WHERE book_id = ?` enforcement and, on Postgres, Row-Level Security.
- Family-mode auth (per-user login, invites, role enforcement) is separate, deliberately deferred
  work that sits above `db/` — explicitly out of scope for this plan (spec's "Explicitly out of
  scope" list: family-mode UI, per-user login, auth rewrite, RLS policies, signup, billing,
  scheduler leasing).
- `core.audit_context.AuditScope` already threads `actor/surface/session_id/sender_id/…` through
  every write; adding `user_id` there is a one-field change the spec notes will light up
  "who did this?" across every Tier-1 write at once, once there is more than one user to ask about.

## Alternatives considered

- **Defer the identity tables entirely until family mode is actually designed and built** —
  rejected: this is the retrofit-cost asymmetry the spec names as the deciding factor. Adding
  `book_id` after the money core already has real user data means a migration across every Tier-1
  table plus every index, and — worse — an open window in which any Tier-1 query written without
  a partition filter is a latent cross-tenant leak that carrying the column from day one prevents
  from ever being writable in the first place.
- **Random (`uuid4`) seed ids for the single bootstrap user/book** — rejected: this plan's whole
  safety story is an exact-equality round-trip diff and reproducible QA clones/fixtures; a
  non-deterministic id would make revision `0001` produce a different database on every fresh
  install and break comparability the same way a non-deterministic timestamp would (the revision
  already hardcodes `_SEEDED_AT` for the identical reason).
- **Put `book_id` on every table, including Tier 2/3** — rejected: contradicts D5 directly.
  `evidence` is explicitly world data — "83,381 rows about the news, not about anyone's money" —
  identical for every user and every book; tagging it with a tenant key nothing would ever query
  by is pure overhead with no corresponding benefit, and the same is true of price history, logs,
  and settings.
- **Build the family-mode auth/permissions surface alongside the identity tables now** — rejected:
  explicitly out of scope per the spec (family-mode UI, per-user login, auth rewrite, RLS
  policies, signup, billing, scheduler leasing all named). The spec's own sequencing note is
  direct: ① Foundation does not yet have owner sign-off on real numbers, so this design buys
  hosted-mode *optionality* at schema cost only and ships nothing user-visible, keeping the
  hardening loop on ① Foundation rather than being pulled sideways into building family mode.

## Agent provenance

- **Decided by:** Claude (implementer agent), storage plan 1 (`lane/storage-plan1` worktree),
  ratifying spec decisions D4/D5 into a formal ADR at task 10, after the identity tables (revision
  `0001`) had shipped and been exercised by the live-data dress rehearsal.
- **Alternatives weighed:** see above — deferring identity entirely was the spec's own named
  rejection (the retrofit-cost asymmetry); random sentinel ids and blanket `book_id` were live
  implementation-time judgment calls consistent with the spec's stated tiering and determinism
  requirements.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
  (D4, D5, the identity-model table, the risk table's `book_id` row); `backend/db/tables.py`'s
  identity + tiering docstring and `DEFAULT_USER_ID`/`DEFAULT_BOOK_ID` constants; `backend/db/
  alembic/versions/0001_identity_and_cutover_ledger.py`.
- **Why this way (one line):** pay one column and one base-class filter per Tier-1 table now,
  deliberately, instead of a whole-ledger migration and an open cross-tenant-leak window later —
  while keeping the tables that don't need a tenant concept (evidence, logs, settings) free of one.
