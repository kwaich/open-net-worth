---
title: Commitment envelopes carry a real linked category and become user-owned
status: Accepted
date: 2026-07-10
deciders: Claude (implementer) + owner (the maintainer)
---

## Context

Since the category-opaque-ids migration (decision #4), `planning.sync_commitment_envelopes()`
minted a **zero-category, bot-managed** envelope per active commitment in the pinned "Commitments"
group, auto-set a monthly target on it, and **auto-retired** it (`force_delete_commitment_envelope`)
the moment the commitment dropped out. Commitment↔category coupling was **deliberately deferred**
(owner, 2026-07-03) "until commitments are properly implemented."

The owner has now specified the intended flow: managing a commitment (setting a **funding target**)
should create a **real envelope + category** for it, shown with the Commitments **Synced** badge,
but **owned by the user** — not auto-managed. This ADR covers the first, concrete slice: giving the
five existing commitments real categories and stopping the sync from auto-managing them. The
full "funding target → creates envelope+category" UI/AI flow is a follow-up.

Owner's live book at migration time: 5 active commitments — `Parkview Residences` (a property
purchase, paid via **transfer** to an off-budget account, whose payment txns already carried the
transfer category `cat_0df7b6f30000`) and four DCA "goals" (`VOO/BTC/QQQ/ETH`, investment buys).

## Decision

We will give each commitment a **real linked category** on its existing envelope and make that
envelope **user-owned**:

1. **Migration** (`backend/scripts/migrate_commitment_categories.py`, one-shot, dry-run default,
   `--apply` backs up `DATA_DIR` first, idempotent): per active commitment with an `envelope_id`
   whose envelope has no category yet — **reuse** the real `cat_<hex>` its own paid txns already
   carry if it is unlinked (Parkview → its transfer category), else **create** a category named
   after the commitment (`purchase/loan_repayment/auto_pay → transfer`, `goal → investment`), then
   `envelopes.add_category(env_id, cat_id)`. No money moves; assigned amounts + targets are
   preserved. Rehearsed on a `/tmp` clone before applying to live.
2. **Sync is HANDS-OFF once an envelope has a linked category** — `sync_commitment_envelopes()`
   no longer auto-sets the monthly target on it, and **never auto-retires** it (the `force_delete`
   branch is skipped, the FK is kept so the **Synced badge stays**). A still-zero-category
   (un-adopted) commitment envelope keeps the old bot behavior (auto-target + auto-retire).

## Consequences

**Positive**
- A commitment's budget row is now a real category the user can rename/recolor/report on, and a
  categorized commitment payment (e.g. Parkview's transfer) drains its envelope like any other.
- A paid-off commitment no longer silently deletes the user's envelope + its assigned money.
- The Commitments group + Synced badge are unchanged visually.

**Negative / Follow-ups**
- Split state: existing commitments are user-owned + categorized; brand-new commitments still get
  the zero-category bot envelope until the **funding-target flow** (which should create the
  envelope+category up front) is built. That flow is the remaining work.
- The sync's `is_commitments_synced` (FK-based) now spans both bot-managed and user-owned
  envelopes — "synced" means "linked to a commitment", not "bot-managed".

## Alternatives considered

- **Keep auto-managing, just add a category** — rejected: the auto-retire would still delete a
  user-owned envelope + money when a commitment is paid off (the owner's core objection).
- **One shared "Commitments" category for all** — rejected: loses per-commitment reporting and
  can't carry the correct per-commitment kind (Parkview is a transfer, the DCAs are investments).
- **Do it all in the going-forward funding-target flow, no migration** — rejected: the owner has
  five live commitments now and asked to migrate them ("create those… in the data").

## Agent provenance

- **Decided by:** Claude (implementer) on branch `feature/ledger-tracking-start`, with the owner
  choosing the per-commitment category kinds (Parkview→transfer reuse, DCAs→investment) after
  inspecting the live data.
- **Alternatives weighed:** see above.
- **Driving artifacts:** owner request 2026-07-10 ("create envelopes+categories for my commitments
  … but don't treat automatically"); supersedes the deferral note in the Commitments section of
  `CLAUDE.md` (2026-07-03).
- **Why this way:** the smallest change that makes the five existing commitments user-owned +
  categorized and safe from auto-deletion, verified on a data clone before touching live data,
  leaving the richer funding-target creation flow as an explicit follow-up.
