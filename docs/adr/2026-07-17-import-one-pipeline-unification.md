---
title: Every import format rides the ONE decision pipeline; manual mappings teach at preview; completeness is gated on the reconcile verdict
status: Accepted
date: 2026-07-17
deciders: senior-engineer lane agent (Claude), executing the owner directive of 2026-07-17
---

# Import one-pipeline unification

## Context

Owner directive (2026-07-17, verbatim mood: "this is not acceptable"): **every** import
format must go through the ONE server decision pipeline — bands, weak twins, ledger check,
picker data, fingerprints, guarded merges, statement records, rollback. No client-side
parallel brain.

The audited reality before this change:

- **Manual-mapped CSV/XLSX** parsed **in the browser** (`importHelpers.analyse()`),
  previewed client-side (`viaLucius:false`), and committed through
  `POST /api/transactions/bulk` — **no `external_id` was ever minted** (future re-imports
  double-booked; the raw bulk endpoint inserts unconditionally by design), dedup ran only
  against the ~75 loaded register rows, no bands/ledger check/statement record/rollback.
  The mapping taught a format profile only at the legacy `/api/import` commit — an endpoint
  the modal never called.
- **OFX/QIF/MT940/CAMT** hit the LEGACY server preview (`import_<fmt>.preview_import` →
  `build_preview`): external_ids minted and per-row anomaly flags attached, but **no
  `match_batch` bands, no weak twins, no ledger check, no statement record**, and the modal
  committed the ≤25-row display `sample` through the client bulk path — a >25-row OFX
  silently booked only its first 25 rows.
- **PDF and unmapped CSV/XLSX** already rode `decision_preview`/`decision_commit` (RFC D7).

## Decision

1. **One entrance.** `api/import_api._preview` routes EVERY format to
   `decisions.decision_preview`; `_commit` (the one-shot `/api/import` + `/api/import/csv`
   aliases) runs `decision_preview` + `decision_commit(default_action="create")`, so even a
   scripted REST import gets a statement record, an import batch (rollback), and hard dedup.
   The commit response is a superset of the old `{created,duplicates,skipped,total_rows}`
   (adds `merged/transfers/import_batch_id/statement_record_id/merge_errors`).
2. **Manual mappings teach the decision entrance** (extends `decision_preview` with a
   `mapping` kwarg rather than a pre-step endpoint — one round trip, no state handoff). The
   mapping is saved as a format profile FIRST (`profiles.learn_from_manual_mapping`, origin
   `manual`, keyed by header-row fingerprint exactly like an inferred profile — SI-16 now
   teaches at PREVIEW time, matching when the auto path learns), then the standard parse
   hits that profile (zero LLM). An explicit mapping BUSTS the cached-parse reuse — a
   hand-map must override whatever an earlier inference cached for the same file. A mapping
   that doesn't line up with any early header row fails LOUD (422), never silently diverges
   from what the user mapped. The schema gains `category_col`; `CanonicalRow` gains
   `category_ref` (the file's own declared category — QIF `L`, a mapped CSV column) resolved
   against the live category store at draft time (unresolvable → dropped, never invented).
3. **Structured formats parse via deterministic canonical adapters**
   (`backend/services/statements/structured.py`), reusing the existing pure per-format
   parsers (`parse_ofx`/`parse_qif`/`parse_mt940`/`parse_camt`) — zero LLM, confidence 1.0,
   unreadable records become explicit skip rows. `CanonicalRow` gains an optional
   `external_id`: the adapters mint the SAME native ids the legacy importers used
   (`ofx:<acct>:<FITID>`, `mt940:<acct>:<sha>`, `camt:<acct>:<ref|sha>`), so files booked
   through either era hard-dedup against each other. MT940 (:60F:/:62F:) and CAMT
   (OPBD/CLBD + FrToDt) adapters extract opening/closing balances + the period, so the
   reconcile identity can prove those statements against themselves; OFX extracts
   CURDEF/DTSTART/DTEND/LEDGERBAL. Per-format `source` stamps are preserved
   (`ofx_import`/`qif_import`/…; csv/xlsx keep `csv_import`).
4. **Reconcile gates completeness** (amends ADR 2026-07-16 batch-aware-dedup):
   `BatchContext(complete=True)` is claimed only when the statement's own reconcile passes
   (`ok`/`ok_if_flipped`) OR the format is a bank-complete machine export (OFX/MT940/CAMT)
   with a declared period and a clean parse (no guard warnings). A failed/unknown reconcile
   → `complete=False`: bands still compute, books-close promotion never fires on unproven
   books. `decision_commit` re-derives the identical claim from the CACHED reconcile verdict.
5. **The client brain is deleted.** `analyse()`, `structExtract()`, the client
   auto-categorizer, `viaLucius`, the modal's legacy `onImport`/`onMerge` commit paths, and
   `AccountsTab.importCommit`'s bulk-POST workaround are gone. The client grid parse
   survives ONLY to drive the mapping FORM (column names). The register's own
   `/api/transactions/bulk` endpoint is untouched — only the import modal's usage died.

## Accepted trade-offs / consequences

- **QIF id-shape change:** the legacy QIF hash folded in the RESOLVED ledger account id and
  category — unknowable at parse time in a parse-once-route-later pipeline. QIF drafts now
  use the standard draft-time content hash (`qif:<sha>` over account|date|note|±amount). The
  ONE crossing re-import (a file previously booked via the legacy path) will not L0-dedup,
  but the batch engine's near-duplicate band catches it loudly (default merge absorbs the
  new id; thereafter L0 holds). OFX/MT940/CAMT ids are byte-identical — no such crossing.
- **CAMT cross-currency honesty:** rows now carry their own `<Amt Ccy>` and route per
  currency; a currency with no wallet is OFFERED but never default-booked. The legacy path
  force-booked the chosen account's currency with the foreign magnitude — silent value
  corruption. Behavior change accepted as strictly more correct.
- **Legacy commit responses widened** (superset keys) — the pinned equality asserts in
  `tests/test_import_api.py` / `tests/test_import_url.py` were updated to the new contract.
- **PDF statements with a printed period but a failed/absent balance check no longer claim
  batch completeness** — books-close promotion now requires proof, per the owner's
  reconcile-gate directive. Bands are unaffected.
- **Known follow-up (explicitly out of scope):** the Lucius `import_transactions` tool
  (`backend/ai/tools/imports.py`) still routes its structured (`ofx/qif/mt940/camt`) and
  mapped-CSV/XLSX branches through the legacy per-format wrappers, and its blind
  `commit:true` for pdf/csv/xlsx through `importer.commit_bytes` — those wrappers stay LIVE
  for the tool only. Unifying the tool is the next slice; its decision-flow branch
  (`statement_id` + `decisions`) already shares these functions.

## Latent bug found and fixed en route

`decision_commit` rebuilt each row's draft fresh (`_row_to_draft`) and thereby DISCARDED the
intra-file disambiguation suffix (`:1`, `:2`…) `_offer_rows` had stamped on content-identical
twin rows — both twins booked under ONE id, so every re-import re-created the second twin
(hard-dedup permanently broken for that file). The commit now reuses the OFFERED draft's
`external_id` verbatim (it is the row's dedup identity; a re-import recomputes the same offer
deterministically, and it stays correct under user edits). Regression pinned:
`tests/test_import_pipeline_unification.py::test_identical_twin_rows_keep_disambiguated_ids_through_commit`
and the pre-existing `tests/dedup/test_e2e_flows.py::test_e2e_import_twins_roundtrip` (which
caught it the moment the CSV route joined the pipeline).

## Evidence

`tests/test_import_pipeline_unification.py` (16 tests: per-format e2e incl. record + rollback
+ re-import dedup, native-id continuity, MT940/CAMT reconcile, QIF category resolution,
teach-then-parse round trip, loud mapping failure, twin-suffix regression, the reconcile-gate
matrix both ways incl. commit re-derivation) · FE `ImportModal.manualRoute.test.tsx` +
rewritten `ImportModal.match.test.tsx` (decision payloads) · acceptance `SI-16` (updated),
`SI-65`/`SI-65a` (new) in `docs/qa/acceptance/statement-import.md`.

### Agent provenance

- **Decided by:** senior-engineer lane agent (Claude), 2026-07-17, executing the owner's
  one-pipeline directive.
- **Lane / branch:** `feat/batch-aware-dedup` (import_api, statements/decisions + parser +
  canonical + importer + profiles + tiers, new statements/structured.py, ImportModal +
  importHelpers + AccountsTab, tests, docs).
- **Why this way:** teach-at-preview via a `mapping` kwarg keeps ONE server round trip and
  makes the taught profile immediately drive the SAME parse the user reviews; canonical
  adapters reuse (never fork) the battle-tested per-format parsers and preserve their id
  namespaces so dedup history survives the migration; the reconcile gate lands inside
  `decisions.py` (the claim's producer) so preview and commit provably claim the same thing.

## Addendum (2026-07-17) — balance-chain completeness for tabular statements

Decision 4 gated `BatchContext(complete=True)` on the reconcile verdict — correct, but it
left every TABULAR source (csv/xlsx) permanently incomplete: a spreadsheet export declares
no opening/closing pair, so its identity sat at `unknown` forever, books-close promotion
never fired, and every wordless unique exact same-day pair landed in "Needs you" (the
owner's complaint: "a lot of unique pairs required to confirm" — live probe
`stmt_ab4a65dc0000`, 06-22 −700,000 VND "Example Diner": unique amount, exact, same-day,
different sources, sat at review).

The fix keeps the gate's own label-free principle: the tabular parse ALREADY extracts a
per-row running `balance`, and an unbroken chain — every row's balance equals the previous
balance plus its signed amount, walked in FILE order (never date-sorted), 0.01 native
tolerance — is per-link proof that no row is missing between first and last: strictly
STRONGER than the endpoint identity, deterministic, zero LLM.
`guards.reconcile_from_balance_chain(rows)` (pure) tries forward/reversed ×
straight/sign-flipped orientations (banks print both ways), pruned by the file's own date
ordering (what keeps a uniform-amount rent/DCA book provable instead of ambiguous); a pass
yields the standard verdict shape (`ok`/`ok_if_flipped`, `method: "balance_chain"`) plus
derived opening/closing and the observed [min, max] window — justified as the period
precisely because the chain proves nothing is missing inside it. It returns None — NEVER a
false claim — on any missing balance/date, fewer than two rows (a single row has no link),
mixed currencies, all-zero amounts, a broken link in every orientation, or two passing
orientations that disagree on opening/closing (degenerate books). Wiring:
`guards.apply_chain_reconcile` runs inside `apply_guards` for tabular tiers only and fills
meta ONLY where absent — a printed/extracted value is never overwritten, and a printed
opening/closing that CONTRADICTS the derived one beyond tolerance keeps `unknown` plus a
warning. Cached pre-fix records self-heal at preview (`decisions._heal_cached_reconcile`,
the `_heal_cached_last4s` pattern): zero LLM, zero re-parse, healed verdict + window
persisted through the existing cache update, so an existing stuck record starts promoting
its unique exact pairs on its very next preview. Pinned:
`tests/test_reconcile_balance_chain.py` (16 tests — the 45-row owner-shape chain;
missing-row / altered-amount / partial-column / single-row / ambiguous / all-zero /
mixed-currency → None; sign-flipped and newest-first files; printed-value contradiction;
and the acceptance in miniature: a chain-proven tabular preview promotes its wordless
unique exact same-day pair to STRONG, the same file with a mid-file row deleted keeps it at
review, and a damaged cached record heals to `ok` with the pair strong).
