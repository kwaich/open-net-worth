# ADR: Budget-envelopes handoff port — envelope-first Budget subtab (delta on the full split)

**Date:** 2026-07-04 · **Status:** shipped on `feature/plan-redesign` (verify wave pending owner sign-off)
**Driver docs:** `frontend/design_handoffs/budget_envelopes_handoff/` (spec + shots + owner-delivered prototype, commit 77bd4f51) · contract `.claude/rfcs/budget-envelopes-handoff-port.md` · parity `docs/launch/BUDGET_ENVELOPES_PORT_PARITY.md`

## Decision

Port the handoff's 15-delta redesign of the Plan → Budget subtab as a faithful prototype port
(zero design drift) on top of the shipped envelope⊥category full split, RETAINING two owner-locked
areas unchanged: the balance-identity RTA calculation/display (richer than the prototype's popover)
and the Plan sticky-header stack. Category deletion moves to the shared 4-kind Manage-categories
modal; the row verb becomes "Delete envelope…"; unlink leaves a category envelope-less (no 1:1
mint — the pre-port semantics are retired); archives carry metadata and are restorable-empty.

## Notable sub-decisions (each with its own evidence trail)

1. **Target due presets, not RRULE** — `docs/adr/2026-07-04-target-deadline-presets-not-rrule.md`.
2. **Move cap = AVAILABLE, negative assigned legal** (amends the original "capped at assigned"
   contract line) — `docs/adr/2026-07-04-move-money-caps-at-available-not-assigned.md`.
3. **Synced (Commitments) envelopes stay offered in the Move/Cover picker** (prototype-authoritative,
   overriding the RFC's original "excluded" line): assigning/moving money INTO a commitment envelope
   is legitimate funding; structural ops (merge/split/delete/group-move) remain refused.
4. **Undo = server-inverse replay, not client snapshots** (the prototype's approach lies once the
   server is truth). Merge and merge-archived-restore offer no one-shot undo (no exact inverse);
   parity doc records the scope.
5. **InflowManager retired** — income categories live in the shared manager; income-GROUP placement
   UI is a recorded loss (Lucius `move_category` still covers it).

## Agent provenance (why this shape)

Orchestrated as parallel worktree lanes against a frozen written contract (map → contract → BE ∥
FE-A → FE-B → merge gates), with the owner live-testing over HMR throughout. Two structural lessons
are recorded for future loops: (a) the owner delivered the real prototype mid-flight — lanes were
re-briefed and trued up rather than restarted; (b) a post-merge 43-agent adversarial bug sweep
confirmed 37 findings (incl. a critical unlocked-store write race that also existed in the
transaction ledger's bulk ops, a dead pre-split label resolver, and several envelope-lifecycle
residues of the old category-first model) — all fixed in three follow-up lanes with per-finding
regression tests. The sweep's residue class validates the owner's instinct that "old category
logic" survives re-architectures in resolver/tooling layers unless explicitly audited: the Lucius
tool surface needed its own lane (`backend/ai/tools/envelopes.py` + description rewrite).
