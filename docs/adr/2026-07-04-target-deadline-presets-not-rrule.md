# 2026-07-04 — Target deadlines: typed presets, not RRULE

**Status:** accepted (budget-envelopes handoff port, Lane BE §9 — `.claude/rfcs/budget-envelopes-handoff-port.md` item 13)
**Where:** `backend/models/budget.py` (`TargetBody`), `backend/services/planning.py`
(`needed_this_month`, `_op_set_target`, `_apply_target_deadline_fields`,
`_valid_due_weekday`/`_valid_due_day`, `_weeks_in_month`), `backend/api/planning.py`
(`PUT /api/planning/targets/{category}`), `backend/ai/tools/planning.py`
(`set_category_target`).

## Context

The design handoff (`frontend/design_handoffs/budget_envelopes_handoff/index.html`,
"Backend contract deltas") *recommends* storing a target deadline as an RRULE string +
DTSTART (`FREQ=WEEKLY;BYDAY=SA`, `FREQ=MONTHLY;BYMONTHDAY=-1`, …) so the UI's three fixed
presets (weekly weekday / monthly day-of-month / yearly date) are really one general
recurrence grammar under the hood. The UI itself, per the same handoff, only ever
renders three FIXED presets — it never accepts or displays an arbitrary rule.

## Decision

Store three flat, typed, validated fields directly on the target spec instead of an
RRULE string:

- `due_weekday: int` (0–6, Monday=0) — **weekly** targets only. Feeds
  `_weeks_in_month(month, weekday)`, which now counts occurrences of the *given* weekday
  instead of always Monday — the ONE place this field has real math effect (a target's
  "needed this month" scales with how many times its due weekday falls in the month).
- `due_day: int` (1–28, or `-1` = last day of the month) — **monthly** targets only.
  Display-only; capped at 28 so every value exists in every month (no Feb-29 edge case).
- `date` (already existed for `by_date`, now also legal on **yearly**) — the annual
  deadline, display-only there (the required `by_date` semantics are untouched).

A field paired with the wrong target type (`due_day` on `weekly`, `due_weekday` on
`monthly`, `date` on `monthly`/`weekly`) is a `ValidationError`, not silently dropped or
coerced. Omitting a field entirely on an existing weekly target reproduces the original
Monday-only week count byte-for-byte (pinned:
`tests/test_envelope_flip.py::test_weekly_target_unset_due_weekday_is_byte_identical_to_before`,
`tests/test_budgeting_acceptance.py::test_BUD_TARGET_DUE_weekly_unset_is_byte_identical`).

## Why not RRULE

1. **The UI never needed the generality.** Every consumer (the target editor, the row
   subtitle "Every Saturday" / "By the 15th" / "By 15 Mar") reads one of exactly three
   shapes. An RRULE parser would be write-only complexity: nothing downstream ever
   constructs or interprets an arbitrary rule string, and the handoff's own admission
   ("unknown rules render read-only") concedes the UI is preset-only regardless of what
   the field stores.
2. **Validation is free with typed fields, expensive with a grammar.** `due_weekday`/
   `due_day` are `int` range-checks; a stored RRULE would need a real parser (or a
   third-party dependency this "leaf" service layer doesn't otherwise need) just to
   validate "is this even one of the three shapes we render," while gaining nothing the
   UI could act on for a rule outside that set.
3. **The math only cares about ONE derived number** (weekday-occurrence count for
   `weekly`) — computing that from an RRULE (`FREQ=WEEKLY;BYDAY=SA`) versus from a bare
   `due_weekday: 5` int is the same amount of code either way; the RRULE buys no
   simplification for the one field that actually feeds `needed_this_month`.
4. **Reversible.** If a real recurrence grammar is ever needed (e.g. "every 3 weeks",
   "the 2nd Tuesday"), it is a strictly additive follow-up: a `rrule` field alongside
   these, resolved into the SAME three primitives (`due_weekday`/`due_day`/`date`) at read
   time — nothing here has to be torn out.

## Consequences

- `needed_this_month`'s weekly branch takes an explicit `weekday` parameter (default
  Monday=0) instead of always counting Mondays — a genuine, tested behavior change, but
  additive: unset is byte-identical.
- `due_day`/`date`-on-yearly are pure storage + echo — zero math change, zero regression
  risk on the recurring/`by_date` branches.
- A future RRULE-based "every N weeks" style target is not blocked; it would layer on top
  of, not replace, these three fields.

## Provenance (Agent)

Implemented by Claude (Lane BE, the budget-envelopes handoff port) per the RFC's explicit
instruction to record this deviation: `.claude/rfcs/budget-envelopes-handoff-port.md`
item 13 states the decision ("UI is preset-only, typed presets validate cleanly, RRULE
derivable later") ahead of implementation; this ADR is the required write-up.
