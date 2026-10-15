---
title: Deleting a budget envelope deletes it — hard-delete with a server-held Undo, not archive-and-restore
status: Accepted
date: 2026-09-26
deciders: implementing agent (senior-engineer lane) + repo owner (design approved before implementation)
---

# 2026-09-26. Deleting an envelope deletes it

## Context

Since the budget-envelopes handoff port, every envelope "delete" (`delete_envelope`) and every
other envelope death — a merge's source, a category rekey's sole-member merge, a category
delete's now-emptied envelope, a dropped-out Commitment's or credit card's synced envelope —
zeroed the row's assigned money, detached its categories, and ARCHIVED it: `archived: true`,
`archive_reason`, `merged_into`, `former_category_ids`, `archived_at`. A parallel
`restore_envelope` un-archived a row (empty, re-linking any still-unlinked former category), and
`list_archived_envelopes` surfaced the tombstones for a restore-footer UI and for Lucius.

The owner's call (2026-09-26): "we should stop archiving envelope, just delete." Archiving every
envelope death forever means the `envelopes` table only ever grows, every reader that lists
envelopes has to filter `archived`, and the mental model — "delete removes it" — is what every
other entity in this app already does (a deleted category, payee, account, automation, rule: all
gone, none archived). Envelopes were the one exception, and the exception bought nothing a
server-held Undo token cannot buy more cheaply: the fresh-start feature
(ADR `2026-09-08-budget-fresh-start-moves-the-floor-not-the-data`) had already solved "let the
user undo a destructive, multi-field budget write" without keeping the old state parked forever
in the table it mutated — the before-image lives one level up, on `plan_state`, under a one-shot
token, and is replayed exactly or not at all.

The RTA-neutrality property this feature depends on (docs/INVARIANTS.md, "Deleting an envelope
never LOWERS Ready to Assign" — pinned as a differential-fuzz property test,
`tests/test_delete_envelope_never_lowers_rta.py`) is about the MONEY MATH — what happens to
`held_in_envelopes`/`held_outside_envelopes`/Ready to Assign when a row's signed `available`
stops being summed — and is completely orthogonal to whether the row's OWN record is archived or
erased afterward. Switching from archive to hard-delete does not touch that math at all.

## Decision

We will make every envelope-lifecycle death a REAL delete of the `envelopes` row, and replace
archive-and-restore with a server-held, one-shot Undo token for the one user-facing delete path.

- `services.envelopes.delete_envelope(envelope_id)` — the only envelope death a human/Lucius
  triggers directly — snapshots the row (`id`/`name`/`color`/`group_id`/`order`/
  `category_ids`), its Zero-Based target, its note, its 50/30/20 cap
  (`budget_engine_state`'s `proportional` row, `caps[envelope_id]`), and every month's
  assigned amount it is about to zero, ALL INSIDE the same `locked_unit_of_work` transaction
  that clears the assignments, pops the target/note/cap, and hard-deletes the row. The
  snapshot is stored under a fresh token via a new `PlanStateStore.set_envelope_delete_undo`
  (`plan_state.payload["budget"]["envelope_delete_undo"]`) — the EXACT sibling of
  `set_fresh_start_undo`, same singleton row, same one-shot/one-pending-at-a-time contract.
  `delete_envelope` returns `{ok, undo_token, former_category_ids, zeroed_assignments}` —
  never the snapshot itself.
- `services.envelopes.undo_delete_envelope(token)` loads that snapshot, re-inserts the
  envelope at its OLD id (so every other reference — a note, a target the caller hasn't
  cleared its own cache of — still resolves), restores its target/note/cap/assignments, and
  re-links any former category that is STILL unlinked (one already re-homed elsewhere is left
  alone) — then consumes the token in the same transaction. A stale/unknown token, or a second
  undo, raises `ConflictError` (409) and writes nothing, mirroring `undo_fresh_start` exactly.
  REST: `DELETE /api/planning/envelopes/{id}` (returns the token) +
  `POST /api/planning/envelopes/undo-delete {token}` (204), replacing
  `POST /api/planning/envelopes/{id}/restore`.
- Every OTHER envelope-death path — `merge_envelope`'s source, `on_category_renamed`'s
  sole-member-merge branch, `on_category_deleted`'s now-emptied envelope,
  `force_delete_commitment_envelope`, `force_delete_card_payment_envelope` — hard-deletes the
  same way (a shared `_hard_delete_envelope(envelope_id, conn=)` helper pops target/note/cap
  and deletes the row, composed into each caller's own transaction) but gets NO undo token:
  only the one path a human/Lucius directly asks for gets a confirm-and-undo affordance: a
  merge is already "not one-shot-undoable" by a separate, earlier RFC decision (its inverse
  isn't a clean per-envelope replay), and the sync jobs are bot-driven reconciliation, not a
  user action to second-guess.
- `restore_envelope` (service function, `envelope_restore` `apply_budget_change` op, REST
  route) and the Lucius tools `restore_envelope` + `list_archived_envelopes` are RETIRED
  outright — recorded in `backend/scripts/tool_renames.py`'s `RENAMES`, `docs/ai/
  TOOL_RENAMES.md`, `docs/ai/TOOL_CATALOG.md`. Past the Undo window (or for an older Lucius
  write), recovery is the generic `restore_checkpoint` every other destructive write already
  has — no envelope-specific recovery tool is worth keeping once nothing archives.
- `list_archived_envelopes` (the READ — service function + `GET /api/planning/envelopes/
  archived`) stays: it is now a permanently historical list of whatever was archived BEFORE
  this change shipped. Nothing new ever lands there. No migration purges the existing archived
  rows — a live book's pre-existing archived envelopes read exactly as before; a separate
  cleanup is the owner's call to make later.

## Consequences

### Positive

- One fewer archived-forever table in the data model, and one fewer "is this row alive"
  filter every envelope reader had to apply going forward (existing readers that still check
  `archived` are harmless, not wrong — see Negative).
- The Undo mechanism is proven infrastructure, not new surface: it is `fresh_start`'s pattern
  applied to a second entity, so its safety property (a client can only ever replay a
  before-image the SERVER wrote, never one it hands back) is inherited, not re-derived.
- The RTA-neutrality property (`tests/test_delete_envelope_never_lowers_rta.py`) required zero
  changes to its own assertions — only its test-fixture reset comment, since the property is
  about the money fold over `_budget_rows()`, not about what happens to the row's bytes after.
- Every envelope-lifecycle death now clears its OWN 50/30/20 cap (`budget_engine_state`), which
  none of the five archive paths did before — a stale cap keyed to a dead envelope id no longer
  sits forever in that engine's `caps` map.

### Negative

- `list_archived_envelopes`/`GET /api/planning/envelopes/archived` and the `archived`/
  `archive_reason`/`merged_into`/`former_category_ids`/`archived_at` fields on `EnvelopeStore`
  are now dead weight for every book created after this ships — kept only because a live book's
  PRE-EXISTING archived rows must keep reading fine, and ripping the fields out would be a
  second, separate, riskier data migration the owner has not asked for.
- The Undo window is real but bounded: an envelope-delete Undo is one-shot and is discarded the
  moment a second delete (of anything) happens, same as `fresh_start`'s. Past that, the only
  recovery is `restore_checkpoint`, which reverts the WHOLE turn, not just the delete.
- `force_delete_commitment_envelope` lost its "batch target-pop, one write at the end of the
  sync" optimization (it is now self-contained, like its card-payment sibling always was) — a
  sync retiring N commitment envelopes in one pass now does N target-store writes instead of
  one. Accepted: commitment counts per book are small, and the alternative was two different
  shapes for the same "hard-delete + clear extras" operation.

### Follow-ups

- A one-time cleanup of the live book's pre-existing archived envelope rows is explicitly OUT
  OF SCOPE here — the owner will approve that separately (CLAUDE.md's write-choke rule: no
  destructive migration without an explicit opt-in).
- `docs/PLANNING_MODEL.md`'s envelope-lifecycle paragraph updated in the same commit.
- **Done (2026-09-28):** the owner approved the one-time cleanup as part of a broader
  data-hygiene sweep. Revision `202609281300_data_hygiene_cleanup` hard-deletes every
  pre-existing archived envelope row (with its budget_assignments, profile target/note, and
  50/30/20 cap) — the exact mechanism `_hard_delete_envelope` already uses for a live delete,
  applied once against the rows the old archive-and-keep-forever behavior left behind. Dry-run
  verified against a clone of the owner's real book: 46 → 26 envelope rows, Ready to Assign
  identical before/after.

## Alternatives considered

- **Keep archiving, just delete the row on a SECOND explicit "empty the trash" action** —
  rejected: this is exactly the two-step model the owner asked to remove; it also doubles the
  number of destructive-action surfaces (delete-to-archive, then archive-to-erase) for no
  benefit once Undo covers the "I didn't mean that" case.
- **Return the before-image to the client and let it POST the inverse back (the old client-side
  Undo shape some other envelope-delete callers used)** — rejected for the identical reason
  `fresh_start`'s pre-merge review rejected it: a client-supplied assignment map lets a forged
  request replay ARBITRARY amounts into ARBITRARY months, wearing an "undo" label. The
  server-held-snapshot pattern already exists in this codebase; reusing it costs nothing extra.
- **Give every envelope death (merge source, sync retirements included) its own undo token** —
  rejected: a merge already has no clean one-shot inverse (RFC decision, pre-dating this ADR),
  and a bot-driven sync retirement is not a user action anyone would expect to "undo" via a UI
  pill — its recovery path (re-create the commitment/card) already exists on its own terms.
- **Purge the live book's existing archived rows in the same change** — rejected: CLAUDE.md's
  write-choke rule requires an explicit opt-in for a destructive migration against real data;
  this ships the mechanism only, leaving existing archived rows exactly as they were.

## Agent provenance

- **Decided by:** implementing agent, senior-engineer lane
- **Lane / branch:** worked directly on `main` (owner-directed, bounded change — no worktree)
- **Alternatives weighed:** two-step archive-then-erase · client-replayed before-image · a token
  per envelope-death path · purging existing archived rows in the same change
- **Driving artifacts:** owner decision ("we should stop archiving envelope, just delete");
  ADR `2026-09-08-budget-fresh-start-moves-the-floor-not-the-data` (+ its amendments) as the
  server-held-undo pattern to copy; `tests/test_delete_envelope_never_lowers_rta.py` as the
  invariant that must survive untouched
- **Why this way (one line):** the codebase already had a proven pattern for "let the user undo
  a destructive multi-field write without keeping the old state parked in the table it
  mutated" — applying it to envelopes needed no new design, only the same discipline.
