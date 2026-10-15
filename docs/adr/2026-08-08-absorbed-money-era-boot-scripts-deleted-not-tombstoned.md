---
title: migrate_investment_kind.py (and its main.py boot block) deleted outright, on live-data evidence — not tombstoned, not deferred
status: Accepted
date: 2026-08-08
deciders: repo owner / controller ruling (D6), Claude (implementer agent, storage plan 3 task 7 — legacy boot-script deletion)
---

# 2026-08-08. Absorbed money-era boot migration scripts are deleted outright, on live-data evidence

## Context

Owner ruling D6 (`docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
Addendum): *"the legacy fail-open boot scripts are DELETED outright once their store is absorbed —
their effects already live in the owner's data; fresh installs start from clean revisions. No
convert-to-revision ceremony."* Task 7's scope was every `backend/scripts/migrate_*.py` module
wired into `backend/main.py`'s `lifespan()` (the fail-open, run-at-every-boot category the design
spec's Problem section names directly — `except: _log.exception(... "non-fatal")`, no ordering
guarantee, no backup) whose target store is now DB-backed.

Two of the three `main.py`-wired scripts were clean single-store cases
(`migrate_drop_payee_txn_count.py`, `migrate_drop_auto_model_switching.py` — both still target
JsonStore-backed stores, so D6's trigger had not fired for them). The third,
`migrate_investment_kind.py`, was a **mixed case**: its `run(commit=True)` touches four stores —
`TransactionStore` (now DB-backed, category `'investment'` cleanup) plus `PayeeStore`,
`BudgetAssignmentsStore`, and `profile.json` (all still JsonStore, Plan 4's wave). The task's own
brief was explicit that a mixed-store script must NOT be deleted or guessed at — it must be flagged
for a controller ruling, because deleting it wrong risks silently losing a still-needed migration
for an install this worktree cannot see (no access to `~/.open-net-worth/`, which QA rules
independently forbid touching).

## Decision

We will **delete `backend/scripts/migrate_investment_kind.py` and its `main.py` boot block outright**
(`git rm`, not a tombstone), on the strength of four pieces of live-data evidence the controller
gathered via read-only probes against the owner's data — resolving the mixed-store concern
rather than routing around it:

1. **The DB half is already fully materialized and provably dead going forward.** The only 5 rows
   with `category='investment'` in the live `transactions` table are ALL `deleted=1` tombstones —
   which the script's own logic (`if not t.get("deleted")`) has always skipped. Zero non-deleted
   rows, zero split lines, carry the old category anywhere.
2. **The JSON halves are already fully materialized too.** Zero hits for `default_category=
   'investment'` in `payees.json`; zero hits across `hidden_categories`/`deleted_builtins`/
   `custom_categories`/`category_targets` in `profile.json`; no `'investment'` key at all in
   `budget_assignments.json`.
3. **The old shape is unreachable forward, by construction.** `'investment'` is not a valid opaque
   `cat_<12hex>` id, and CLAUDE.md's own invariant — an unknown category id/label RAISES at the
   create choke — means no future write can ever re-mint the pre-opaque-id shape this script exists
   to clean up. A fresh install starts empty; nothing remains for Plan 4 to inherit.
4. **The boot block was an active hazard, not a dormant one.** It called `run(commit=True)` at
   EVERY process start: a full-table read of the DB-backed `transactions` store, plus a latent
   `replace_all()` full-table rewrite reachable OUTSIDE both the ledger choke and Alembic, wrapped
   fail-open (`except Exception: log`). This is precisely the shape D6 exists to remove. Corroborating,
   independently of the live-data probes: the script's own CLI `main()` was already broken —
   `result['rules_deleted']`/`result['rules_disabled']` are keys `run()`'s summary dict has never set
   since the rules-tier removal, so any actual hand invocation with `--commit` (or without) would
   raise `KeyError`. The boot path never called `main()` (it called `run(commit=True)` directly), so
   this dead branch was invisible at runtime — corroborating "no manual use" from a source this
   worktree COULD verify independently, without live-data access.

**The deleted rows are historical audit data, left untouched** — the migration itself deliberately
refused to rewrite `deleted=True` rows (point 1 above), so deleting the now-redundant migration
CODE does not touch, and was never going to touch, any stored data.

`migrate_drop_payee_txn_count.py` and `migrate_drop_auto_model_switching.py` are KEPT — both still
target JsonStore-backed stores (Plan 4's wave); D6's "once their store is absorbed" trigger has not
fired for either yet.

## Consequences

### Positive

- One fewer fail-open, full-table-read, choke-and-Alembic-bypassing boot hazard runs on every
  startup.
- The codebase's actual behavior now matches its stated story ("revisions 0001+ are the only path
  for fresh installs") for this store, rather than carrying a second, silently-dead code path that
  claims to still matter.
- The deletion is evidence-backed on FOUR independent axes (DB state, JSON state, forward
  unreachability, and hazard/no-manual-use), not a single check — a future reader auditing this
  decision has more than one thing to re-verify if they doubt it.

### Negative

- This ruling required live-data read access a plain implementer agent does not have (and, per QA
  rules, must not have) — the controller had to adjudicate it personally. An agent facing a
  similarly mixed-store script in a future task cannot self-resolve it the way this one was
  resolved; it must again be flagged and routed to someone with that access, which is real friction
  the "flag, don't guess" brief rule accepts deliberately.
- Deleting the script means its four-store cleanup logic is gone from the tree — if a NEVER-SEEN
  install somehow still carries the pre-opaque-id `'investment'` shape (contradicting all four
  evidence points above), there is no automated repair path left; recovery would require restoring
  the file from git history and running it by hand.

### Follow-ups

- A "sweep the rest of `backend/scripts/`" pass is a separate, larger decision than this task
  authorized: several standalone (non-`main.py`) scripts import from each other in a dependency web
  (e.g. `migrate_checkpoint_blobs_external_id.py` depends on three others), so they are not
  independently deletable even where each individually touches only a now-DB-backed store. Noted by
  task 7, not acted on.
- `services/ledger.py`'s `resync_transfer_base_stamps` is ALSO a fail-open one-time boot block
  touching the now-DB-backed `transactions` table, but it lives inside a shared service module, not
  a `backend/scripts/migrate_*.py` file — `git rm` does not apply to it, and this task's Files line
  does not reach it. Flagged by task 7's implementer, not resolved here; needs its own D6-shaped
  ruling in a future task. **RESOLVED (plan 3 task 9 security-gate fix wave):** the function and
  its `main.py` boot try/except block were deleted outright — the DB `TransactionStore` no longer
  vetoes a transfer's `base_amount` (`transaction_store.py`), so the drift this healed is
  unproducible going forward, and it was the last boot-time fail-open full-table `replace_all` on
  `transactions` bypassing the ledger choke and audit trail.

## 2026-08-09 update — the two remaining scripts resolved (plan 4e task 3, the terminal wave)

This ADR's own "Decision" section left `migrate_drop_payee_txn_count.py` and
`migrate_drop_auto_model_switching.py` KEPT, on the grounds that D6's "once their store is
absorbed" trigger had not fired for either yet. By plan 4e (Wave E), it has fired for the first
but not the second — the two scripts now split:

- **`migrate_drop_payee_txn_count.py` — DELETED** (`git rm`, plus its `main.py` boot block and its
  test `tests/test_migrate_drop_payee_txn_count.py`). Its only target, `PayeeStore`, is now
  DB-backed (`BookPayloadTableStore`, plan 4d). D6's trigger has fired: the one-shot `txn_count`
  strip is doubly moot going forward — the DB row shape simply doesn't carry stray legacy keys the
  way a hand-edited JSON file could, and the `Payee` model's `extra="ignore"` (ADR-cited in the
  script's own docstring) already drops the field silently on any parse/dump cycle even if one
  did. Confirmed no other importer: `grep -rn migrate_drop_payee_txn_count backend tests` is empty
  after the deletion.
- **`migrate_drop_auto_model_switching.py` — KEPT, unchanged.** Two of its four targets
  (`preferred_models`, `automations` config) are now absorbed, but its **chat-session
  "auto"-sentinel strip is still load-bearing**: chat-session storage
  (`backend/ai/assistant.py::_index_file`/`_save_session`) is still a plain file, not a
  `JsonStore` subclass and not part of this program's retirement scope (confirmed structurally —
  no `JsonStore` import anywhere in `backend/ai/assistant.py`'s session-index path). A stale
  session row still carrying the literal string `"auto"` in its `model` field would resolve to
  that literal as a model id, since nothing routes `"auto"` any more post-ADR
  `2026-08-06-auto-model-switching-removed` — the hazard this script exists to clean up. It also
  does not import `json_store` at all (confirmed by grep), so its presence does not block this
  wave's terminal deletion of `backend/db/json_store.py` either way. The extract-the-sliver
  refactor (making the script target only the chat-session file, once the other two targets are
  gone) is explicitly NOT done here — it touches the chat subsystem, which is a separate future
  concern outside this task's Files line.

This worktree has **no live-data access** (`~/.open-net-worth` is read-only and out of reach for
probes, per this program's QA rules) — unlike the original ruling above, which rested on the
controller's live-data read-only probes against the owner's tree. This update's finding is
**structural/static evidence only** (grep + AST-level confirmation of what imports what, and what
storage substrate each target now uses) — not the same live-data confidence the original D6 ruling
had for `migrate_investment_kind.py`. If a future reader wants the live-data-backed version of
this confirmation for `migrate_drop_auto_model_switching.py`'s remaining targets, it has not been
done and would need the same controller-probe treatment this ADR's original decision received.

## Alternatives considered

- **Flag-and-defer to Plan 4** (the implementer's own first-pass instinct, per the brief's "do not
  guess" rule) — superseded once the controller's live-data evidence resolved the mixed-store
  concern: there is no reason to carry a proven-inert, fail-open boot hazard forward another plan
  once the evidence already shows nothing depends on it.
- **Turn `run()` into a no-op instead of deleting the file** — rejected: D6 explicitly says "deleted
  outright … no convert-to-revision ceremony"; an inert-but-present script is exactly the dead
  weight the ruling exists to remove, and it would still be a full-table read reachable at boot
  (even a no-op body inherits the call site's cost of invoking it) unless the boot block were removed
  too — at which point the file itself serves no purpose remaining in the tree.
- **Delete only the `main.py` boot block, keep the script file for potential CLI use** — rejected:
  the CLI path was independently proven broken (point 4 above), so there is no live caller left to
  preserve the file for; keeping a broken, uncallable script around is worse documentation than
  deleting it, since git history already preserves it if a future reader needs to see what it did.

## Agent provenance

- **Decided by:** repo owner / controller (the live-data-evidence ruling, quoted verbatim in
  storage-plan3 task 7's own report), implemented by Claude (task 7 implementer agent); this ADR
  authored by the task 8 implementer agent per the plan's explicit deferral ("ADR'd in T8").
  Lane/branch: `lane/storage-plan1` (worktree `storage-plan1`).
- **Alternatives weighed:** flag-and-defer was superseded by the live-data evidence directly
  resolving the concern the brief asked to flag; a no-op stub was rejected against D6's explicit "no
  convert-to-revision ceremony" wording; keeping the file for CLI use was rejected once the CLI path
  was independently proven broken.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
  (D6, the Addendum's D6 simplification); storage-plan3 task 7 report, which quotes the controller's
  ruling verbatim and records the independent CLI-breakage confirmation
  (`.superpowers/sdd/2026-08-07-storage-plan3-money-core/task-7-report.md`); the deleted file's git
  history (`backend/scripts/migrate_investment_kind.py`, recoverable via `git log --diff-filter=D`
  if ever needed).
- **Why this way (one line):** a fail-open boot script whose live-data footprint is provably zero on
  every axis that matters (DB, JSON, forward-reachability, manual use) is not a risk worth carrying
  past this plan just because it once touched more than one store.

