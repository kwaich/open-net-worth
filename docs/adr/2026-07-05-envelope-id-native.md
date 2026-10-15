# 2026-07-05 — Money is envelope-id native (shim removal + fail-loud guard)

**Status:** accepted (owner decision, 2026-07-05) · retires the category-key↔envelope-id
dual-key facade described in `.claude/rfcs/envelope-not-category.md` /
`envelope-category-full-split.md`
**Where:** `backend/services/planning.py` (storage facades), `backend/services/envelopes.py`
(`assert_no_category_keyed_remnants`, resyncs removed), `backend/main.py` (boot guard),
`backend/scripts/migrate_envelope_id_native.py` (one-shot), `backend/scripts/golden_budget_snapshot.py`

## Decision

Assigned amounts and targets are stored keyed **only** by envelope id (`env_<hex>`). The
category-key↔envelope-id translation shim is DELETED:

- `planning._budget_key` and every category-keyed read fallback are gone; the assigned/target
  read+write facades resolve an envelope id directly (`month_map(m).get(env_id)`).
- The runtime self-healing resyncs (`resync_legacy_category_keyed_assignments`,
  `resync_legacy_category_keyed_targets`, `resync_orphaned_budgeted_categories`,
  `stamp_missing_envelope_placement`, `_normalize_member_remnants`,
  `_normalize_target_note_remnants`) are REMOVED — they existed only to fold a
  still-category-keyed book into envelope slots at boot.
- `ensure_envelopes_migrated` keeps ONLY its fresh-install seed (mint 1:1 envelopes when
  `envelopes.json` is empty); it no longer re-keys anything.
- A one-shot `migrate_envelope_id_native.py` re-keys the single real book directly (the live
  book was already 100% env-keyed, so it only re-keys the one `category_notes` remnant and
  drops three dead profile fields).

Because the resyncs are gone, a book that STILL carries a category-keyed remnant would silently
render that money as $0. To make that fail **loud** instead, `main.py` calls
`envelopes.assert_no_category_keyed_remnants()` at boot (outside any try/except): if any
non-zero assignment key or any target key is not `env_`-prefixed, it RAISES and refuses to boot,
naming the offenders and pointing the operator at the migration.

## Why

The dual-key facade was a backwards-compat shim carried "in case" a book wasn't migrated. On a
single-owner instance that is dead weight and a correctness footgun — the facade silently
masked which representation was authoritative. Owner directive: remove the shim, migrate the
data, keep the codebase clean. The invariant is proven byte-identical on the real book by
`golden_budget_snapshot.py` (old-code vs new-code, and pre- vs post-migration, both diff empty).

The guard is the deliberate replacement for the deleted self-healing: fail-closed on money
integrity (refuse to boot, name the offenders) beats fail-silent ($0). It never fires on a
migrated or fresh book; it only catches a genuine disaster state — an old backup, a checkpoint
rollback to a pre-cutover point, or a clone from a stale snapshot.

## Consequences

- Money code is simpler and single-representation; `budget_month` for a 1:1 book stays
  byte-identical to the pre-split table (pinned by `tests/test_envelope_migration.py`).
- A pre-cutover book must run `migrate_envelope_id_native` before it can boot — a deliberate,
  loud, one-line operator step, not a silent runtime fallback.
- Verified: golden snapshot identical (refactor + migration), pytest green (no
  planning/envelope/budget/category regressions), `ruff` clean, import-linter 5/5 contracts kept.

## Agent provenance

Built as the "envelope-id-native" lane. The runtime silent-$0 risk was caught by the blocking
code-review gate and closed with the boot guard + this ADR before merge, rather than shipping a
refactor that could render wrong numbers on a non-migrated book.
