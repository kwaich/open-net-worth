# ADR: Unified statement import — P1–P3 implementation decisions

**Status**: Accepted · **Date**: 2026-07-12 · **Deciders**: Fable (implementing agent), under
the owner's standing delegation ("implement everything, all phases except P4, decisions yours")
**Context**: RFC `.claude/rfcs/statement-import-unified.md` (Accepted). This ADR records the
non-obvious choices made DURING the build — the RFC records the architecture.

## Decisions (Agent provenance)

1. **`StatementParseError` subclasses `ImportError_`** — one exception family means every
   surface (REST import endpoints, the chat tool, import_pdf's facade) maps parse failures to
   a 422/ok:false without per-module translation. `StaleSchemaError` extends it for the
   re-learn loop.
2. **The tabular tier does NO per-row LLM work** — clean-payee extraction is a document-tier
   feature; a tabular row's payee comes only from a real counterparty column (`payee_col`).
   This preserves the cost promise (one schema call, any file size) rather than quietly
   re-introducing per-row model calls for cosmetics.
3. **`parse_signed` treats zero as "no value" for transaction cells, but statement META keeps
   an explicit 0** (`_coerce_meta.money`) — an empty amount cell means "not a transaction";
   an opening balance of exactly 0 is a statement fact the reconcile identity needs (found by
   test: a 0.00 opening silently downgraded reconciliation to "unknown").
4. **Needs-your-eyes = confidence < 0.6 OR any guard note**, and it exists only in the
   DECISION flow: rows are offered, default-skipped, user-includable. The legacy preview/commit
   paths keep P0's hard-skip semantics so existing clients/tests see identical behavior.
5. **Vision rows are capped at confidence 0.95** — a vision read never claims certainty, so a
   model that returns 1.0 still leaves headroom for guard downgrades and honest UI display.
6. **`decision_commit` recomputes duplicate/twin state server-side from the CACHED rows** —
   the client sends only decisions (row index + action + edits); it cannot forge dedup state,
   and re-committing the same statement L0-dedups to zero. Merge decisions snapshot the FULL
   pre-merge transaction row on the batch (`import_batches.merges[]`); `rollback_import_batch`
   restores those rows exactly (un-merge). Deliberate scope cut: RESTORING a rolled-back batch
   re-creates created rows only — merges stay un-merged (the response says so) because
   re-applying a merge on a row the user may have since edited is not safely automatic.
7. **Format-profile lookup fingerprints each of the first 12 rows** (header position is
   unknown before a schema exists); saving fingerprints the DETECTED header row. An explicit
   `schema` argument (a taught tabular skill / API caller) bypasses memory reads AND writes.
   `tests/conftest.py` isolates the profile store per test — memory is a parse side-effect and
   must never leak between tests (or into the live tree).
8. **Statement skills are ORDINARY skills** (owner correction, same day — SUPERSEDES the
   first-cut taught-profile design): regular SkillStore skills marked
   `metadata.statement_format="true"` (+ optional `account_id` scope), authored via the normal
   `create_skill` tool / Skills UI, listed/edited/deleted like any skill, visible in the `/`
   picker. The parser bridge (`statements/skills_service.guidance_for`) injects applicable
   skills' bodies (bounded ~3.5k chars, account-scoped first) into the document-tier prompts.
   The four bespoke `*_statement_skill` tools were removed (pinned by
   `test_no_bespoke_statement_skill_tools_exist`). Auto-learned format MEMORY stays in
   `StatementFormatStore` — it is a cache the user never authors, not a skill.
9. **The teach gate moved from code to playbook**: the managed `statement-import` skill now
   instructs the drafting flow — preview the example, CHECK the reconcile verdict, only then
   `create_skill` — because with ordinary skills there is no bespoke save chokepoint to hard-gate.
   Safety holds structurally: a skill only steers extraction prompts, and every import still
   passes the deterministic guards + review before money books.
10. **Chat-tool preview caps its reply at 80 rows** (`rows_truncated` marker) — decisions
    reference server-side row indexes, so the model can still commit ALL rows; only its
    *display* payload is bounded to protect context.
11. **`/api/import/preview` stayed one endpoint** — the decision payload is ADDITIVE on the
    legacy PreviewResult dict (old clients read `sample`/`total_rows` unchanged; the modal
    upgrades when `statement_id`/`rows` are present). Blind `/api/import` commit kept for
    back-compat; both first-party surfaces now use `/api/import/commit` (decisions).
12. **Test-suite ports, not deletions**: `tests/test_reconcile.py` §10–20 pinned reconcile's
    now-moved internals — replaced by equivalents in `tests/test_statements_parser.py` +
    `tests/test_reconcile_fence.py` (fencing, NFKC-expansion, money-path pins carried over
    verbatim); the old fence tests' guarantees were preserved, including the sanitize-cap
    sized off the NORMALIZED length.

13. **Dedup-engine field fixes (2026-07-13, from the owner's live bug report — 1/22 known
    duplicates flagged, and that one WRONG):**
    - **F20** — `kind=adjustment` rows (incl. every Starting-Balance opening txn) never
      classify: a €4.72 opening balance FX-coincided with a S$6.80 coffee and classified as
      its TRANSFER_PAIR. An adjustment is a balance correction, not a movement.
    - **F21 (wording-drift tier)** — near-duplicate no longer REQUIRES note similarity: the
      same account, same-currency amount within 1%, SAME DAY is duplicate evidence on its own
      (review-capped, never strong). The owner's history was hand-logged with cleaned notes
      ("MPOS*Cinebar") that fail token-Jaccard against the statement's raw descriptors
      ("MPOS*CINEBAR Q HOAN KIEM") — 19 of 22 real duplicates were invisible to the gate.
    - **TRANSFER_LEG_ECHO surfacing** — the engine classified the card-payment credit
      (S$800.00 "Meridian Bank Visa Direct") as an echo of the booked bank→card transfer, but
      `import_anomalies` only surfaced NEAR_DUPLICATE/TRANSFER_PAIR and dropped it. Now a
      first-class preview flag (`transfer_leg_echo`, "Already booked as a transfer").
    Live verification after fixes: 21/22 statement rows flag against their true existing
    twins; the single unflagged row is verifiably absent from the ledger.

## Consequences

- One parser feeds import, reconcile, chat, and skills; `import_pdf._llm_extract`,
  reconcile's three parse paths, and the direct-Anthropic vision call are gone.
- Scanned PDFs import end-to-end; CSV/XLSX auto-read without a mapping; repeat tabular
  imports are zero-LLM (proven by stub-counter tests).
- Money-path coverage: golden fixtures (MoMo status/id/balance, VCB bilingual+VN thousands,
  EU locale, ambiguous dates, polarity, page-split, span-verification), decision-commit
  (uncapped 30-row, merge+unmerge-rollback, L0 re-commit), enrichment parity
  (preview category == commit category), skills gate. Suites at completion: backend
  9218+ passing, frontend 1990+ passing, `make lint` + 5 import contracts green.
- Known follow-ups (not regressions): `make qa-e2e`/`make ui-gate` runs against the live app
  for `docs/qa/acceptance/statement-import.md`; `equity_import.py` still carries its own
  text-extract copy (RFC non-goal); disagreement-escalation (D4.7) is specified but not built.
