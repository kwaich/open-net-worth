---
title: Import teach lane — routing rules route at draft birth (label-only choke preserved); upload-time instructions are fenced + profile-persisted
status: Accepted
date: 2026-07-17
deciders: senior-engineer lane agent (Claude), executing the owner-approved teach lane
---

# Import teach rules ("this is a transfer to X, always") + upload-time instructions

## Context

Two deterministic teach features on top of the unified import pipeline (ADR
2026-07-17-import-one-pipeline-unification):

1. Live mis-booking: Meridian Bank rows `Ecom.EW….MOMO.….CashIn.…` draft as EXPENSES; the
   truth is a transfer to the owner's MoMo Wallet (`CashOut` = wallet→bank). Alert-booked
   copies already exist as `kind=transfer` bank→MoMo (source=ai), so the wrong drafts also
   dodge dedup (near-dup is expense/income-only).
2. Sources need standing owner guidance the LLM parse should see ("rows marked HOLD are
   pending — skip them") without re-typing per import and without granting text any tool
   power.

The LLM free-text entrance ("tell Lucius in prose, it compiles rules/instructions") is a
LATER slice — deliberately out of scope here; both features are its deterministic
substrate.

## Decisions

1. **Routing is a rule ACTION, applied at import draft-birth ONLY.** The spend-rules
   engine gains an additive optional action `set.route_to_account: <account_id>`
   (rule_store `norm_set`, `validate_rule` with a caller-passed `valid_accounts` set —
   same pattern as `valid_categories`). The reshape lives in
   `statements/importer.py::_apply_routing`, called inside `_row_to_draft` (ONE draft
   mapper — decision flow and the legacy tool facade behave identically): first matching
   enabled routing rule wins; money-out row → transfer FROM the statement account TO the
   routed account; money-in row → the reverse; the rule's label half (category/tags/
   note_append) applies here too, since the choke will never re-run it.
2. **The create choke stays LABEL-ONLY (standing invariant).** `rules_service.apply_rules`
   skips a routing rule WHOLESALE (`is_routing_rule`) — no half-applied label, no `stop`
   side-effect, no usage bump. Re-shaping legs at the choke would let a rule mutate
   manually-typed and AI-created transactions; routing is an import-draft concern. Pinned:
   `test_create_transaction_never_routes_and_routing_rule_is_fully_inert`.
3. **Dedup identity is routing-invariant.** The routed transfer draft keeps the plain
   draft's `external_id` verbatim — a file imported before the rule exists still L0-dedups
   after it, and vice versa. Against an ALREADY-BOOKED identical transfer (the alert-booked
   case) the engine can't help (transfer-vs-transfer is outside `match_batch`'s single-leg
   contract), so routed drafts get the FX-pair precedent instead: a deterministic
   booked-transfer finder (`_find_booked_transfer`, shared with `_existing_pair_transfer`)
   flags `transfer_leg_echo`, blind commits default-SKIP, and an explicit merge is accepted
   only against the commit-side RE-DERIVED id (never caller-trusted). Routed (two-leg)
   drafts are excluded from `match_batch`'s candidates — the engine judges single-leg
   statement rows; a two-leg incoming would echo-claim against its own id-less draft.
4. **Honest fallbacks over guessing.** No routing when the target account is missing,
   archived, the statement-side account itself, or holds a DIFFERENT currency (one
   statement row cannot invent the other leg's converted amount — the FX-pair machinery,
   which sees both legs, owns cross-currency transfers). FX-pair rows are never routed.
5. **Amount conditions match native at draft time.** `_amount_for_match` falls back to the
   native leg magnitude when `base_amount` is absent (import drafts are matched BEFORE the
   choke stamps base). Choke-path behavior is unchanged (base is always stamped by then);
   a legacy unstamped ledger row now compares native instead of never matching — accepted
   as strictly more useful.
6. **Instructions are OWNER-authored but STILL fenced.** They arrive through a form field
   (not the approved-skill path), so they are sanitized + nonce-fenced
   (`core.untrusted.fence_untrusted_block("owner-guidance", …)`) and capped at 1000 chars,
   threaded into exactly TWO LLM calls — the row-extraction prompt (tier2/tier3) and the
   category-hint batch — with framing that says "reference data, never a task change".
   ZERO tool-scope changes. Deterministic parses store them without using them (the
   response says which happened: `used_in_parse`).
7. **Instructions persist on the format profile, keyed like mappings.** Tabular sources
   attach to the header-fingerprint profile (the same row the schema lives on — a first
   csv parse re-stamps after the profile is learned). Document sources key by a TEMPLATE
   fingerprint: the first digit-free lines of the extracted text (masthead/headings —
   anything dated/numbered is excluded wholesale, so next month's statement hashes
   identically). A scanned PDF with no extractable text has no stable key: instructions
   apply in-request but cannot persist, and `PUT /api/statements/{id}/instructions`
   reports `saved:false` instead of pretending.
8. **Editing instructions never re-parses.** The review meta line's edit saves via the PUT
   endpoint and steers the NEXT fresh parse — an automatic re-parse would re-bill the LLM
   on every text tweak. The cached-parse reuse is NOT busted by instructions (unlike a
   manual mapping, which must override a cached inference).

## Consequences / accepted trade-offs

- A routed transfer draft's category-hint is skipped (an own-wallet transfer needs no
  suggestion) — saves hint-batch tokens.
- The teach popover's derived match is a PROPOSAL (payee if present in the descriptor,
  else the longest all-caps digit-free token) and is always shown editable — a
  direction-specific token like `CashIn` can be derived on ugly descriptors; the user
  edits before saving.
- Routing rules created by the teach UI scope `match.account_id` to the statement-side
  account (descriptor grammars are bank-specific); the category twin is global.
- The Lucius `import_transactions` tool's legacy blind-commit branch applies routing at
  draft-build (shared `_row_to_draft`) but has no booked-echo default-skip — same
  pre-existing gap as its missing bands (unification ADR's known follow-up).

## Addendum (2026-07-17, owner-requested, same day)

- **Remembering is user-controlled** (amends #7): the choose step gains a "Remember for
  this source" checkbox (checked default; `remember_instructions` form field, default
  true). Unchecked = ONE-OFF: the text applies to the run identically but is never
  persisted, and already-saved instructions survive untouched (the scanned-PDF
  apply-without-save shape, now selectable).
- **Changed instructions DO bust the cached parse** (supersedes #8's "the cached-parse
  reuse is NOT busted by instructions" — live repro: instructions typed for an
  already-uploaded file steered nothing because no parse ran): the record stamps
  `parse_instructions_hash` (the guidance the cached parse consumed); an effective-hash
  mismatch — or an explicit `force_reparse` (the meta line's "· Re-read" link) —
  re-parses honestly (re-bills, shown on the AI-cost fact) and re-opens the hint gate.
  Identical instructions stay cached/free; the PUT edit endpoint itself still never
  re-parses (#8's cost rationale holds — the bust lands at the NEXT preview).

## Follow-up (explicitly out of scope)

The LLM free-text entrance: a prose instruction ("MoMo top-ups are transfers to my
wallet") compiled by Lucius into a routing rule / stored instructions with a confirm
diff. This slice ships the deterministic storage + application layer it will target.

## Addendum (2026-07-17, same day): the free-text entrance ships — SI-68, ops-only

The follow-up above is CLOSED by a live repro: the owner typed the exact MoMo
CashIn/CashOut mapping into the instructions box (mirroring #1's fixture) and was furious
the rows still drafted as plain expenses — instructions only ever steer the parse + label
hints (decision #6), they cannot change row STRUCTURE, and nothing told him that boundary
existed. Two design calls were made and then amended once more before shipping:

1. **Ops applied directly, not a proposed-rule confirm step.** The original framing
   ("Lucius drafts rules, the user Creates them via `POST /api/rules`") was replaced
   mid-build: typing the instructions (or pressing Re-read) IS the confirm — a SEPARATE
   per-suggestion approval step would just be a second place to ignore the same warning
   the owner already ignored once. The pass instead applies a closed vocabulary of ops
   (`route_transfer` / `set_category` / `skip`, each carrying the model's `reason`)
   DIRECTLY to the offered drafts, through the identical `route_transfer_draft` reshape
   core decision #1's routing rules use (extracted out of `_apply_routing` so both
   callers share one guarded implementation) — hard-validated (unknown account/category,
   an out-of-range or already-covered row, more than one op per row → dropped silently,
   never applied wrong) and fully visible: a ✦ provenance chip with the model's own
   reason on every touched row, an applied-strip summary, and a per-row/all undo
   (`DELETE /api/statements/{id}/lucius-ops[?row=]`, a server round-trip — the applied
   ops are cached on the statement record and re-applied verbatim at
   `decision_commit`, so "not these" must be recorded server-side, not just hidden
   client-side). The review table + the SI-64 verdict + commit stay the one real gate,
   same as every other draft field.
2. **No rule-minting.** An earlier iteration additionally had the pass emit a `remember`
   list compiled into standing `RuleStore` rows (`source="instructions"`) so a future
   import needed no LLM call at all. Dropped before shipping (owner sequencing call:
   "make it work before rules get involved") — the pass's memory is instead the SI-67
   per-source instructions persistence already in place: the same guidance re-feeds the
   pass on every future import of the source, and its own cache (`lucius_pass_hash`,
   mirroring `parse_instructions_hash`) means an unchanged re-preview costs zero LLM
   calls regardless. Rules stay a purely deterministic, manually-taught mechanism
   (decision #1's Teach popover, `POST /api/rules`) — untouched by this slice.

New surface: `backend/services/statements/lucius_pass.py` (the prompt + hard validation +
appliers), `DELETE /api/statements/{statement_id}/lucius-ops` (undo), the `lucius`/
`lucius_pass` preview-payload fields, and the `statement_lucius_pass` cost-attribution
source / `task_models` key (added to `VALID_TASKS` alongside the other `statement_*`
stages, `news_screen`'s precedent). No new AI tool scope, no new tool-dispatch surface —
this is a preview-time enrichment of the existing decision pipeline, same trust boundary
as SI-67's two prompts.

## Addendum (2026-07-17, a reported bug): the persist contract hardened + source-peek prefill

The owner's real Meridian Bank XLSX exposed a silent data-loss hole in decision #7's
persistence, plus a UX gap that made even WORKING persistence look broken:

1. **Learn/lookup window invariant** (bug fix). The profile learner keyed the source by
   its header row at ANY index (`learn()` fingerprints `grid[schema["header_row"]]`,
   uncapped), while every lookup — `find_profile`, `_instruction_fingerprints` — scanned
   only `grid[:_MAX_HEADER_SCAN_ROWS]` (12). The Meridian Bank export's 12-row preamble put
   its header at grid index 12, one past the window: the profile became WRITE-ONLY —
   re-learned (re-billed) on every import with `uses` forever 0, and instructions typed
   with "Remember for this source" CHECKED silently dropped (a tabular source never
   doc-mints, so `remember_instructions` returned None). Fix: `_MAX_HEADER_SCAN_ROWS`
   raised to 40 — covering schema inference's whole 30-row sample
   (`tiers._MAX_SCHEMA_SAMPLE_ROWS`, invariant pinned by test) — and `learn()` now
   REFUSES a header past the window (returns None): no memory beats broken memory, and a
   learned profile is STRUCTURALLY findable. A pre-fix orphan self-heals on its next
   import (the widened scan finds it); no data migration.
2. **Saved instructions pre-fill the choose step** (amends the #7 UX). Saved guidance
   used to apply invisibly (blank box → profile fallback), so the owner read the empty
   textarea as "my instructions vanished" even when persistence worked.
   `POST /api/import/source-peek` (fingerprint-only — `profiles.peek_source`, ZERO LLM,
   no statement record, no writes; unknown/unreadable → `{known: false}`, never a picker
   error) now runs on file pick and the modal PRE-FILLS the textarea under a dim
   `✦ Saved for this source` hint — visible, editable, re-saved per the checkbox. Typing
   always beats a slow probe reply (stale-sequence + typed guards client-side).

## Evidence

`tests/test_import_routing_rules.py` (13: fail-first expense→transfer flip, direction-aware
CashOut, commit + re-import L0, echo default-skip + accepted merge + refused invented id,
choke inertness, cross-currency/archived/self/FX-pair fallbacks, min-amount at draft time,
store round-trip, REST validation) · `tests/test_import_instructions.py` (6: fenced in both
prompts, profile persist + next-import auto-apply, cap at store+prompt, hint-batch alone,
deterministic store-only, PUT round-trip incl. clear/422/CSRF/404) · FE
`ImportModal.teach.test.tsx` (7) + `ImportModal.instructions.test.tsx` (4). Acceptance:
SI-66/SI-67 in `docs/qa/acceptance/statement-import.md`.

Addendum: `tests/test_import_lucius_pass.py` (9: fail-first plain draft → direction-aware
`route_transfer` ops with reasons + booked transfers on the owner's exact live-incident
text; unknown account / out-of-range row / unresolvable label / an already-booked
duplicate's op all drop silently; malformed reply → zero ops, preview still succeeds;
compile-once caching + recompile-on-change by LLM-stub call count; one-off instructions
feed the pass without persisting; saved instructions auto-feed it on the next import;
`set_category`/`skip` ops apply, skip default-skips at commit, overridable) · FE
`ImportModal.luciusPass.test.tsx` (8: honesty caption, applied strip with/without a
breakdown, per-op-type provenance chip, skip toggle, per-row + Undo-all). Acceptance:
SI-68 in `docs/qa/acceptance/statement-import.md`.

Live-incident addendum: `tests/test_import_instructions.py` (7 more: fail-first
deep-preamble persist + saved-schema reuse on the owner's exact flow, fresh-profile stamp,
learn round-trip + past-window clamp, window-covers-inference-sample pin, peek
known/unknown/unreadable/no-writes/CSRF, and the type → preview(remember) → peek
round-trip regression guard) · FE `ImportModal.instructions.test.tsx` (4 more: prefill +
hint, edited prefill round-trips with remember, failed peek harmless, slow peek never
clobbers typing).

### Agent provenance

- **Decided by:** senior-engineer lane agent (Claude), 2026-07-17, executing the
  owner-approved teach lane (routing rules + upload instructions; LLM entrance deferred).
  Addendum decided the same day, closing the deferred LLM entrance (SI-68).
- **Lane / branch:** `feat/batch-aware-dedup` (rules service/store/API/tool, importer +
  decisions + profiles + parser + tiers + format store, import API, ImportModal +
  importHelpers + AccountsTab, tests, docs; addendum adds
  `services/statements/lucius_pass.py` + the same import API/ImportModal/importHelpers
  files + `db/model_config_store.py`'s `VALID_TASKS`).
- **Why this way:** route-at-draft-birth keeps ONE reshape site upstream of
  matching/bands/commit so routed transfers dedupe like any other draft, while the
  label-only choke invariant stays provable by a one-line skip; instructions reuse the
  format-profile memory (one "this source" concept for mappings, schemas, and guidance)
  and the house fence so owner text can steer reading without ever gaining instruction
  power over tools.
