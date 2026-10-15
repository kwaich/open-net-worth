---
title: PDF statement import enriches rows (clean payee, foreign charge, ref-based dedup) with a RAW note + a payee-name write-choke input
status: Accepted
date: 2026-07-12
deciders: orchestrating Claude agent + repo owner (owner decisions, 2026-07-12: clean-payee+keep-raw, ref-based dedup key, full E2E both paths)
---

# PDF statement import enrichment: raw note, clean payee, foreign charge, robust dedup

## Context

The LLM PDF importer (`services/import_pdf.py`) extracted only `{date, amount, direction,
description, confidence}`. Three real gaps surfaced from a live Meridian Bank Visa statement:

1. **A false "0 to import / 0 transactions read"** even when the model returned rows. Two
   independent bugs stacked here:
   - The frontend `importPreview` handler (`AccountsTab.tsx`) read the wrong response keys —
     `drafts`/`total`/`skipped`/`anomaly` instead of the server's `sample`/`total_rows`/
     `skip_samples`/`anomaly_summary` (a serialised `PreviewResult`). `drafts` was always
     empty → the modal rendered 0. This was the actual user-visible failure.
   - `import_pdf._llm_extract` accepted ONLY a bare top-level JSON array, so any model
     (grok, Sonnet, …) that wrapped the array in an object (`{"transactions":[…]}`) or emitted
     reasoning prose containing a stray `{` before the array parsed to 0 rows.
2. **Foreign amounts discarded** — a VND/KRW pre-conversion charge in the descriptor was
   dropped; the app has `original_amount`/`original_currency` fields for exactly this.
3. **Messy payee** — the raw acquirer descriptor ("MPOS*CINEBAR Q HOAN KIEM") became the
   payee via the deterministic normaliser, which only lowercases/strips (→ "mpos cinebar q
   hoan kiem"), never "Cinebar".

Both surfaces — the Accounts ImportModal AND the Lucius chat drop-file path — funnel through
`import_pdf.preview_import`/`commit_import`, so the fixes had to be parser-level to reach both.

## Decisions

- **Robust reply parsing.** `_llm_extract` now unwraps an object-wrapped array, accepts a bare
  single object, and falls back to a balanced-bracket scan that pulls the first valid JSON
  array out of surrounding prose. A genuinely empty reply still reads as a legitimate 0.
- **Enrich extraction.** The prompt now also returns `payee` (clean name), `original_amount`/
  `original_currency` (foreign charge), and `reference` (bank Ref No.).
- **Note stays RAW; the clean payee rides `payee_id`.** The stored `note` is the verbatim bank
  descriptor. This is deliberate: the cross-source match engine (`services/matching.py`)
  **hard-gates near-duplicates on note-text similarity** (`_classify`), so a cleaned note
  ("Cinebar") would fail to pair a PDF row with a bank-feed line ("MPOS*CINEBAR …") for the
  same charge — silently double-booking a user who has both a statement import and a bank sync.
  Keeping the raw note preserves that pairing AND literally keeps the raw text.
- **New general write-choke input: a `payee` NAME.** To show a clean merchant while keeping a
  raw note, `create_transaction` / `bulk_create_transactions` now resolve a caller-supplied
  `payee` name into a payee entity when no `payee_id` is given (precedence: `payee_id` > `payee`
  name > note), then drop the transient key (never a stored column). Added to the REST
  `TransactionCreate` schema so the client-commit path (`/api/transactions/bulk`) uses it too.
  This is reusable by any importer/AI caller, not PDF-specific.
- **Dedup key: bank Ref No. when present, else content hash.** `external_id` becomes
  `pdf:ref:<account>:<ref>` when the row carries a reference (survives statement re-export
  reformatting — the strongest same-source key), else the prior `pdf:<sha of date·desc·signed
  amount·account>`. Cross-SOURCE dedup (PDF vs bank feed) is NOT done via this id — bank
  connectors don't expose their reference as a matchable id and not all banks have one — it
  stays the fuzzy match engine's job (amount ±1% / date ±8d / account / raw-note similarity),
  which runs on both commit paths via `review_created_rows`.
- **Client commit forwards `external_id` + foreign charge.** Previously the modal's
  `importCommit` dropped `external_id`, so a re-import double-booked; it now forwards
  `external_id`, `original_amount`, `original_currency`, and the `payee` name, so the client
  path stores the byte-equivalent row the server-side commit would.

## Consequences

- Both import surfaces get clean payees, foreign amounts, and robust dedup with one parser change.
- The shared preview projection (`import_common.display_draft`) gained optional `payee` /
  `original_amount` / `original_currency` / `reference` / `external_id` fields (None for plain
  CSV/OFX) — the preview sample shape widened; the six per-format `test_preview_sample_shape`
  tests were updated.
- The money write choke gained a small, general, well-tested capability (name→payee_id) rather
  than a PDF-specific hack. Display-only draft keys are stripped before the choke so `**data`
  can't persist them.
- Not addressed here: `PREVIEW_LIMIT = 25` still bounds the committable sample on the
  client-commit path, so a >25-row statement would import only 25 via the modal (the Lucius
  chat path commits server-side and is unaffected). Flagged for a follow-up.

Tests: `test_import_pdf_service.py` (robust parse + enrichment + no-junk-keys + ref dedup),
`test_add_transaction_payee_id.py` (payee-name choke), `test_matching_engine.py`
(raw-note cross-source pairing), `importHelpers.preview.test.ts` (response mapping). Full
backend suite (9198) and frontend (1976) green; verified live against the Meridian Bank statement.
