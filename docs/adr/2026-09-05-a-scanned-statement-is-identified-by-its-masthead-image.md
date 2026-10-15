---
title: A scanned statement is identified by its masthead image, looked up by unique nearest key
status: Accepted
date: 2026-09-05
deciders: orchestrating agent (Claude) + repo owner
---

# A scanned statement is identified by its masthead image, looked up by unique nearest key

## Context

A statement source is remembered under a fingerprint in `statement_formats`: the header-row
hash for a tabular export, the digit-free head of the extracted text for a document
(`profiles._document_fingerprint`). Every per-source memory hangs off that row — the accounts
chosen last time (pre-filled in the Import modal), the owner's "Remember for this source"
notes, the cached match policy.

A bank that renders its PDF as one image per page produces no text at all. `pypdf` extracts
nothing, the parser renders the pages for vision (`_pdf_to_upload`, `kind: "pdf_pages"`), and
the import itself works — but `_document_fingerprint` is None, `_instruction_fingerprints`
returns `[]`, and every consumer silently no-ops: `peek_source` answers "unknown" each month,
`ensure_source_profile` returns None so `remember_accounts` has no id to write to, and the
checkbox writes nothing. Reported case: five monthly statements of one card imported into the
same account since April, the account re-picked by hand every time, no profile row ever minted
(the ledger held 138 rows from that source and `statement_formats` held nothing for it).

The module comment accepted this ("a scanned PDF with no extractable text has no stable key at
all"). It was true of TEXT keys only.

## Decision

We will key an image-only PDF on a perceptual hash of its masthead — the rendered top quarter
of page one — and resolve that key by a unique nearest neighbour within a small bit distance.

- **The key.** `services/statements/page_hash.masthead_fingerprint`: page one rendered by
  pymupdf at a fixed 0.5 scale, grayscale, `clip` to the top 25 % of the page (only the band is
  rasterized; the scale is capped so an oversized page cannot allocate more than a few hundred
  KB); the band is reduced to an 8 × 16 grid of cell means and a dHash (each cell against its
  right neighbour) gives 120 bits → `img:` + 30 hex. Pure Python over `pix.samples`; no numpy,
  no PIL. None for a non-PDF, an empty document, or a band whose hash has fewer than
  `_MIN_BITS = 12` bits set OR fewer than 12 clear: a near-blank masthead sets a handful of
  bits and sits within tolerance of every other near-blank one, and a band under a monotone
  brightness ramp (uneven lighting on a photographed page, a header gradient) sets nearly
  all of them, since every right-of-left comparison tilts the same way — either pole would
  let one document claim, then overwrite, the profile of the next. This is the module's own
  render at fixed parameters, deliberately not
  the parser's — the hash is an identity and must not move when the parse rendering changes
  (the scar `test_statement_fingerprint_stability` guards for the text key).
- **Where it applies.** `profiles._instruction_fingerprints` falls back to the image key ONLY
  when both text keys are None and `fmt == "pdf"`. A PDF with text keeps its `doc:` key; the
  image key is a fallback, never a second identity (pinned: the hash function is monkeypatched
  to raise for a text source).
- **The lookup.** `profiles._lookup(store, fp)`: exact match first, then — for an `img:` key
  only — the uniquely nearest `img:`-keyed profile within `page_hash.MAX_DISTANCE = 2` bits.
  Equal nearest candidates are ambiguous and return no hit. A stored
  key that fails `page_hash.is_image_key` (prefix, exact lowercase hex of the right width —
  `int(x, 16)` would accept a signed key that XORs to within a bit of any probe — and the
  balance floor) is never a near hit. Every
  document-path consumer (`find_instructions`, `find_match_policy`, `remember_match_policy`,
  `peek_source`, `ensure_source_profile`, `remember_instructions`) reads through it; the
  tabular `find_profile` does not.
- **The tolerance, from the drift, not the gap — and tight.** On the reported source, five
  monthly statements hash to at most 1 differing bit between any pair (36–37 of 120 bits set);
  twelve synthetic monthly variants of one template drift 0; unrelated real documents sit
  40–49 bits away. But synthetic sources that share a template and differ only in the bank's
  name and address sit 5–13 apart, and a finer grid (12 × 16 … 16 × 32) does not widen that
  gap. The TEXT key separates those two sources — name and address are digit-free template
  lines — so an image tolerance inside that range would merge what the text key keeps apart:
  at 6, 6.5 % of pairs in an 80-source template family merged; at 2, 0.95 %; at 0, 0.16 %.
  The costs are asymmetric: a stray profile costs one re-pick of the account, a merged one
  pre-fills another bank's accounts and steers the parse with its instructions and cached
  match policy. So `MAX_DISTANCE = 2`, twice the observed drift. A source that drifts further
  (a promo line measured 4–5 bits, a 3 px offset 6) mints a stray profile, which is the safe
  failure.
- **The fingerprint never renders a page.** `parser.pdf_text` is the extraction half of
  `_pdf_to_upload`; `profiles._template_text` reads it once and derives both text keys from the
  one extraction. Before, the peek — on every file pick, on the event loop — went through
  `to_upload` twice, which rasterizes every page of a scan to images the fingerprint never
  reads: 1.6 s for a five-page statement, unbounded for a long one.
- **No re-key on a near hit.** The first month's key stays the anchor; a later month within
  tolerance resolves to it and does not move it, so the identity cannot drift by walking.
- **No migration.** Nothing was ever stored for an image-only source, so there is nothing to
  migrate; the first import after this change mints the profile and the month after pre-fills.

## Consequences

### Positive

- Closes the loop for image-only PDFs with the same profile row every other source uses —
  accounts, notes and match policy all start persisting with no frontend change.
- ~8 ms per hash on a real statement, bounded memory, no new dependency (pymupdf is already
  the parser's renderer).
- The identity is independent of OCR: a bank's re-scan at a different DPI still hashes the same
  because the cell grid is proportional to the page.

### Negative

- A second identity notion beside `doc:`; a reader of `statement_formats` now meets two key
  shapes (`is_image_key` tells them apart).
- Unique-nearest lookup is a scan over image-keyed profiles — O(profiles) per lookup. Fine at tens of
  sources; an index would be premature.
- A bank that changes its masthead (rebrand, new logo) reads as a new source, exactly as a
  heading change does for the text key. The owner re-picks the accounts once.
- Two statements from one bank whose mastheads differ only below the top quarter (e.g. two
  products with the same header) resolve to ONE profile. Accepted: that is also what the text
  key does for shared template text.

### Follow-ups

- Verify on the live book: the first import after deploy mints the profile; `POST
  /api/statements/source-peek` for the next month answers `known` with the card account.
- If a real source ever drifts past 2 bits month to month, revisit the band and grid before the
  tolerance — widening it is what lets two banks on one output engine collide.
- `_pdf_to_upload` still renders every page of a scan for the parse, uncapped; and the peek's
  remaining cost is pypdf's per-page extraction (~110 ms per image page), which the text key
  cannot avoid — every page must be read to prove a scan has no text. The peek endpoint now
  runs the fingerprint in the threadpool. Pre-existing and still on the loop: `_resolve_source`'s
  synchronous `fetch_url_bytes` (20 s timeout) and `preview_import`'s whole decision pipeline,
  including its own fingerprint calls and the parse render.

## Alternatives considered

- **OCR the masthead and reuse the text key** — rejected: no OCR dependency in the venv, an
  LLM vision call at peek time costs money and latency for a probe that must be free, and OCR
  output of a logo is not stable.
- **Hash the whole first page** — rejected: the body changes every month (rows, totals); the
  masthead is the template-stable part, the same reasoning that keeps the text key to the
  digit-free head.
- **Exact-match the image key** — rejected: one bit of drift would mint a stray profile beside
  the remembered one each month — the exact scar the text key's legacy-key walk exists for.
- **Never let a near hit drive a write** (lookup by distance, mint by equality) — rejected: the
  second month's "Remember for this source" must land on the first month's profile, or it mints
  the stray row beside it — the scar again.
- **Set the tolerance from the gap to other banks (12, a tenth of 40–49), or at six times the
  drift (6)** — both rejected after measurement: same-template different-name documents sit
  5–13 apart, so either merges two banks on one output engine (44 % / 6.5 % of pairs in an
  80-source family), and the text key separates exactly those pairs.
- **Floor on set bits only** — rejected: it guards the blank pole and not the saturated one; a
  brightness ramp mints `img:fff…f` for every document under it (three different banks measured
  4–8 apart at 104–111 bits set). The floor is on balance, `min(ones, clear) ≥ 12`.
- **De-trend the rows so a ramp cancels** (row-median or endpoint-slope thresholds) — rejected:
  measured, it keeps ramped sources 15–26 apart but costs the same-source stability the key
  exists for — median doubles the real-month drift (1 → 2), slope makes a 3 px offset cost 16
  bits instead of 6. A ramped photograph is not the input this identity is for; refusing it is
  the safe failure.
- **Key on the file's first bytes / producer metadata** — rejected: producer strings are shared
  across banks using the same output engine and change with its version.
- **Render through the parser's `_pdf_to_upload` output** — rejected: the parse rendering is
  free to change (DPI, colour); an identity must not.

## Agent provenance

- **Decided by:** orchestrating Claude agent + repo owner ("why is his statement not selecting
  lark?")
- **Lane / branch:** `feat/scanned-statement-identity`, worktree `onw-lane-scanned-identity`
- **Alternatives weighed:** OCR · whole-page hash · exact image match · producer metadata ·
  parser rendering
- **Driving artifacts:** the Import modal with an empty account picker for a known source;
  `tests/test_scanned_statement_identity.py`; measurement across five real monthly statements
  (hashes only, never the content)
- **Why this way (one line):** the only month-stable thing about an image-only statement is
  how its masthead looks, so that is the key — and a key that can drift by a bit is looked up
  by distance, not equality.
