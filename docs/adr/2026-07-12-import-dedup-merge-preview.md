---
title: Import preview surfaces already-present transactions with a per-row Skip / Merge / Import-new choice
status: Accepted
date: 2026-07-12
deciders: orchestrating Claude agent + repo owner (owner decisions, 2026-07-12: don't double-book; let the user skip OR merge; keep the statement's final settle amount + the existing row's enrichment)
---

# Import preview: skip / merge / import-new for already-present transactions

## Context

A user who both hand-enters transactions (via Lucius) AND imports statements has heavy
overlap: a real charge is often already in the ledger before the statement is imported. Two
gaps made this painful:

1. The preview's "already imported" count reflected only **exact `external_id`** matches. The
   pre-existing rows were added manually (`source=ai`, `external_id=None`) or from email
   (`external_id=email:…`), so nothing matched — the preview said "0 already imported" and the
   user would double-book on import.
2. The **fuzzy match engine did run** in the preview (`import_anomalies.detect_anomalies` →
   `matching.check`), but its matches were classified `transfer_pair`/`near_duplicate` and only
   (a) rendered as a vague advisory ("… flagged for review", with a leading-dot bug when the
   only flags were transfer_pairs — a type the copy didn't enumerate) and (b) never excluded, so
   the rows still imported as new.

The owner asked for a precise flow: don't double-book; let the user **skip** a match, **merge**
it (keeping the statement's final settle amount AND the existing row's enrichment), or **import
it as new** — per row.

## Decisions

- **The match flag now carries the existing row's summary.** `import_anomalies` attaches
  `existing_id` + a compact `existing` projection (note, signed amount, currency, has_category)
  to each `near_duplicate`/`transfer_pair` flag. It rides the preview `sample` (flags already
  flow through `display_draft`), so the client shows old-vs-new with no extra endpoint.
- **A matched row is SKIP by default** (never double-book), with a per-row `Skip / Merge /
  Import new` control and bulk "set all". Skip leaves the existing row untouched; Import-new
  creates a duplicate deliberately; Merge folds in.
- **Merge is a deliberate, conservative reconciliation** — new service `ledger.apply_import_merge`
  (REST `POST /api/import/merge`):
  - The **statement wins on the money**: the existing row's amount is updated to the imported
    final settle amount — but ONLY when both are the same single-leg kind (expense/income). A
    two-leg **transfer's legs are never rewritten** (the movement is already booked); it is
    linked + cleared + enriched only.
  - The **existing row keeps its enrichment** (category / note / tags / payee) — these are not in
    the patch, so `update_transaction`'s `{**existing, **changes}` rebuild preserves them.
  - The **import fills gaps**: the pre-conversion foreign charge is copied in only when the
    existing row lacks it (never overwrites an existing one).
  - **Idempotency**: the import's `external_id` is appended to the existing row's `external_ids[]`
    (so a future re-import of the same statement is an exact L0 no-op), and the row is marked
    `cleared` (it appeared on a statement → it settled).
- **The advisory warning was split from the exclusion.** "Already in your account" is now the
  dedicated skip/merge banner; the amber advisory covers only the still-imported anomalies
  (intra-file repeats, unusually large amounts) and no longer starts with a bare ".".

## Consequences

- The preview is honest: a Meridian Bank statement over a book with manual entries flags the 9 rows
  the user already has, each showing the matched existing row, defaulting to skip.
- `apply_import_merge` edits an existing ledger row — gated to the safe reconciliation above and
  covered by `tests/test_import_merge.py` (amount-wins, enrichment-preserved, foreign-fill-only-
  when-missing, transfer-legs-untouched, id-linked, endpoint smoke). The fuzzy match can
  false-positive, which is why the default is skip and every row is user-visible + overridable.
- Not done here: an "undo merge" affordance beyond the existing transaction edit history, and
  auto-merge without review (kept manual — a fuzzy match is advisory, never silent).

Frontend: `ImportModal.match.test.tsx` (skip-default / merge-routes-to-onMerge / import-new).
Backend + frontend suites green; verified live against a Meridian Bank statement.
