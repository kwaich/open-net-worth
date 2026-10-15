---
number: N/A
title: One covered-set derivation on both sides of the commit (statement ledger-diff)
status: Accepted
date: 2026-07-29
deciders: Phase-4 implementation lane (Claude), closing divergence #5 of RFC 2026-07-29
---

# One covered-set derivation on both sides of the commit

**Implements** the Phase 4 half of `.claude/rfcs/2026-07-29-one-match-engine-instructions-as-policy.md`
("switch `uncovered_ledger_rows`' `_covered_ids` onto `_apply_batch_match`"), the divergence §1b
names last and calls *quieter*. **Extends** `docs/adr/2026-07-29-one-matching-engine-instructions-as-policy.md`
decision 1 ("one assignment, inverted for the reconciliation view") to the post-commit surface.

## Context

"What does this statement account for?" was asked twice and answered by two different engines:

| | Pre-commit (`preview_ledger_check`) | Post-commit (`uncovered_ledger_rows` → `_covered_ids`) |
|---|---|---|
| Pairing | the flags `decision_preview` built via `_apply_batch_match` (`matching.match_batch`) | its own `import_anomalies.detect_anomalies` scan (per-row `matching.check`) |
| Weak settled-drift band | yes (`weak_tier=True`) | **no** |
| Owner `MatchPolicy` | yes | **no** |
| Global 1:1 assignment | batch-wide | per-row claims + a local exclusivity adapter |
| Routing rules / Lucius ops | applied | **not applied** |

The divergence was not theoretical — two tests in one file asserted opposite answers for the
identical Grab fixture (an agent-booked S$13.71 estimate against the statement's S$14.07 settled
figure, 2.6% apart): `test_multi_account_import.py::test_preview_carries_the_ledger_check` pinned
the entry as **covered** (weak band), while `::test_ledger_diff_surfaces_uncovered_rows` pinned the
same entry as **uncovered** after commit. The owner reconciling a statement therefore saw one
"In your ledger, not on this statement" list in the preview and a different one on re-opening it —
the same class of lie (two derivations of one fact) the RFC exists to kill, on a quieter surface.

## Decision

1. **One shared re-derivation: `decisions.rederive_match_pass(record, rows, account_id)`.** It
   rebuilds the cached parse the way the preview did — routing rules at draft birth,
   `_apply_batch_match` (batch engine, weak tier, the record's stamped policy), then the reviewed
   Lucius ops — and returns `(ctx, results, lucius_ops, routing)`. `decision_commit` was already
   doing exactly this inline and now calls it; `ledger_diff._covered_ids` calls the same function
   instead of `detect_anomalies`. Two consumers, one derivation.

2. **`_attach_booked_echo_flags` stays OUT of the shared pass**, attached by each caller at the
   same point in its own sequence as the preview attaches it. Folding it in would silently widen
   `decision_commit`'s merge acceptance set: `assigned_by_index` is read off the flags *before*
   those echo flags attach, so an FX-pair booked twin would have become an accepted merge target
   without anyone deciding that. The one thing this ADR would not let itself do for tidiness.

3. **A stale `match_policy_hash` withholds the panel** (`{"status": "stale_policy", "rows": []}`)
   instead of falling back to `MatchPolicy()`. `decision_commit` already refuses the same stamp;
   the reasoning transfers because a looser policy covers MORE entries, so the fallback would make
   this panel **under-report** what is unaccounted for. Both frontend consumers already act only on
   `status === "ok"`, so this renders as "no panel", never as a wrong panel.

4. **`import_anomalies.detect_anomalies` and `matching.check` are untouched.** They still serve
   `import_common.build_preview`, `match_review` and `bulk_add_transactions`, and stay pinned
   byte-identical by `tests/dedup/test_char_statement_match_surface.py`. This change removes a
   *caller*, not a surface.

## Consequences

### Positive
- The panel agrees with the preview by construction, proven by
  `tests/test_ledger_diff_covered_parity.py::test_post_commit_panel_agrees_with_the_preview_on_a_weak_band_entry`
  (fails on the pre-change code with the drift entry as the extra id).
- Three fixes ride along for free, because they were all in the same missing derivation: the
  owner's match policy, the routing rules, and the reviewed `reject_match`/`force_match` ops now
  all shape the post-commit panel exactly as they shape the preview.
- The money tie-out agrees too — `_totals` reads the same offers, so a weak-matched row counts as
  booked on both sides instead of unbooked here and booked there.
- **Cheaper, not slower.** 90-row statement against a 3,000-txn ledger: retired scan ≈ 835–1054 ms,
  shared pass ≈ 224–273 ms (~3.9×). `match_batch` reads ±`FETCH_WINDOW_DAYS` (8 days) around the
  rows; `detect_anomalies` read a 120-day lookback. Even with a non-default policy — which costs a
  second `match_batch` for the suppression diff — it is ≈ 352 ms.

### Negative
- `ledger_diff` now depends on four names from `decisions` instead of two. The dependency direction
  is unchanged (both are `services/`), but the module is no longer readable without the matcher's
  contract in mind.
- The post-commit panel now inherits the commit path's stale-policy refusal: a statement committed
  under a policy whose skill layer is later edited shows no panel until re-previewed. Withholding
  is the honest failure, but it is a failure the old panel did not have.
- Parity holds *for an unchanged ledger*. A commit that books rows legitimately changes the answer
  (a created row is a new, real transaction; its drifting twin then genuinely is unaccounted for) —
  the parity test therefore skips every row at commit, and that is the only regime in which "the
  two panels must be equal" is a meaningful claim.

### Follow-ups
- R3's `statement_twin.booked` case ("your ledger already has this from an earlier import") is
  still the accepted degrade — the RFC's option (b), a second explicit inversion through `check()`,
  remains unbuilt and out of scope here.
