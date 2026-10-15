---
number: N/A
title: Statement instructions become deterministic match policy — extraction, layering, and the two things that make a tightening knob dangerous
status: Accepted
date: 2026-07-29
deciders: Phase-3 implementation lane (Claude), applying the pre-build security review of RFC 2026-07-29
---

# Statement instructions become deterministic match policy

Implements §D5 (policy extraction), §D6 (layering) and §D7 (the `lucius_pass` prompt trim) of
`.claude/rfcs/2026-07-29-one-match-engine-instructions-as-policy.md`, under that RFC's ⛔ SECURITY
REVIEW block. Follows `docs/adr/2026-07-29-weak-tier-is-a-keyword-and-the-commit-acceptance-set-narrows.md`,
whose "Follow-ups" entry named exactly this work and its requirements #5, #6, #7, #10 and #11.

## Context

Before this change the only mechanism turning *"no drift in date"* into behaviour was
`lucius_pass`'s prompt: it asked the model to emit `reject_match` for one of four enumerated
reasons, three of which are mechanical (a currency lookup, a day-gap subtraction, a boolean). That
is a per-row LLM judgment surface, re-billed and re-derived on every import, for arithmetic that is
free and auditable.

This phase adds an LLM call **whose output becomes engine behaviour on a money-movement path**, so
the design question is not "does extraction work" but "what can a mis-derived or injected policy
reach". Two things about the surface make that sharp:

- `metadata.match_policy` on an ordinary skill is a **persistent, AI-writable channel** —
  `update_skill` takes no confirmation. Skill metadata is untrusted input, not configuration.
- The instructions text is fenced, but attacker-influenced in the general case: a PDF's own printed
  text reaches the same prompt.

And the RFC's own safety argument was **wrong**. §D1/§D5 both leaned on *"every knob is
monotone-tightening, so no policy can widen the blast radius."* That holds in candidate-set space
and fails in outcome space, which is the space that books transactions.

## Decision

### 1. One flat, fully-required schema — and never `oneOf`

`match_policy.py` makes ONE bounded `core.llm_seam.complete_json` call (source key
`statement_match_policy`, notes fenced via `core.untrusted`), against a schema obeying the four
rules `lucius_pass._DECISIONS_SCHEMA` already pays for: no `oneOf`/`allOf` (constrained decoders
cannot enforce "exactly one branch"; OpenAI strict mode 400s on it; on this repo's model it
silently corrupted output while the model's own reasoning stayed correct); **every** property in
`required`, with optionality expressed as a nullable type union (a property the schema does not
force the model to WRITE is one it will skip — that mistake cost 16 correctly-decided rows); a
required free-text `analysis` declared FIRST; and a **lenient** `validate` predicate (shape only —
rejecting the reply over one bad field would destroy every correctly-extracted setting beside it).

`services/` reaches the provider through `core.llm_seam` directly, never `ai/` (contract 3).

### 2. Every untrusted layer passes ONE allowlist

`extract_fields` reads five names and nothing else, so an unknown key is dropped *structurally*
rather than by a denylist that has to stay in sync. It is applied identically to the model's reply
and to skill metadata, and it is idempotent, so a blob cached under an older build cannot smuggle a
key that is no longer legal.

`weak_tier` is absent from `_EXTRACTABLE_FIELDS` **and from the model-facing schema** — it is not a
`MatchPolicy` field at all (the preceding ADR made it a call-site keyword), so no extractor can
reconstruct it. `tests/dedup/test_match_policy.py::test_weak_tier_is_not_a_policy_field` stays green
unmodified.

### 3. Account NAMES in, routed-set resolution out — no ids in either direction

The extraction prompt carries **no account list of any kind**. The model may only name accounts the
owner's own notes name; those resolve server-side, case-insensitively, against ONLY the statement's
routed accounts. An unresolvable name is **dropped, never blocked-by-default** — blocking on an
unrecognized name would let a typo, or an injected line naming a non-existent account, suppress
matching wholesale.

### 4. Layering: most specific wins on numerics, but no layer may un-tighten

§D6's four layers stack broadest first, field by field. One deviation from a literal reading of
"most specific wins": **only the tightening side of each boolean is accepted** (`currency_strict=True`,
`drift_matching=False`). The loosening values ARE the defaults, so honoring them buys no
expressiveness — it only creates a channel for a narrow layer (or an injected one) to un-tighten a
broad one. The numerics do follow most-specific-wins, bounded on every side by the engine's own
class caps, which a policy can never exceed.

### 5. The cache stores the EXTRACTION, at two levels, with a retry bound

`StatementFormatStore` gains `match_policy` (`{"fields": {...}, "note": str}`, account **names**
still names) + `match_policy_instructions_hash`, written in a single `set_match_policy` save so the
pair is transactional (a failed write leaves both unset and the next preview re-derives — security
F7). Caching the extraction rather than a resolved policy keeps the cache independent of routing and
makes commit's re-resolution exact.

The profile is the **cross-file** cache. A **same-file** level on the statement record was added
after the first build broke `test_import_instructions.py`: R6 is right that
`remember_instructions` stores nothing for a csv/xlsx with no profile and `_document_fingerprint`
returns `None` for a short/scanned PDF, so a profile-only cache re-derived on **every preview of
the same file**. The record now stamps `match_policy_source` + `match_policy_note` +
`match_policy_instructions_hash` and is checked first.

A miss on both is bounded by `_MAX_POLICY_ATTEMPTS = 3` (`match_policy_attempts` on the record) —
the `hints_attempts` precedent, for the mirror-image reason: a preview is model-driven and
retryable, so a source whose extraction keeps coming back malformed would re-bill forever. Unlike
the hint cap, running out is safe by construction: the preview stamps the policy it **actually
matched under**, so commit re-resolves to the same one and agrees rather than silently widening.

### 6. `match_policy_hash` covers the resolved policy + the routed account set

Not `parse_instructions_hash`, which covers one layer of four: editing a skill's
`metadata.match_policy` between preview and commit moves the acceptance set while leaving that hash
untouched, and so does re-routing the statement (which changes which blocked names resolve). The
record also stamps `match_policy_source` — layer 4's own fields — so `decision_commit` re-resolves
every deterministic layer with **zero LLM**, honoring its "never re-parses, never re-bills"
contract. A mismatch REFUSES; an absent stamp with a present `parse_instructions_hash` REFUSES.

There is deliberately **no `except → MatchPolicy()`** anywhere on the commit path. Absence yields
the *loosest* policy, which accepts merges the reviewed preview refused — the dangerous direction.

### 7. A tightening policy is LOUD, not safe — the correction that matters most

The deleted safety argument fails twice:

- **Tightening manufactures duplicates.** `decision_commit`'s `default_action` is `"create"`. A
  policy of `max_date_drift_days=0` suppresses every drift match, yields a preview that looks
  perfectly *clean*, and books a re-import of last month as N brand-new transactions.
- **Tightening redirects merges.** Removing a row's true candidate hands it the global assignment's
  second-best — a different, real ledger row — which `apply_import_merge(facts="statement")` then
  re-dates and re-amounts.

So `_flag_policy_suppressions` runs the batch a second time at `MatchPolicy()` (pure compute, and
only when a policy is actually in play) and reports every row whose assignment **disappeared or
changed**: `match_policy.suppressed` on the preview, a per-row `policy_suppressed` flag carrying the
suppressed entry's own summary, and — the part that stops the money bug — `draft["policy_suppressed"]`,
which makes those rows **default to SKIP instead of create**. Explicit inclusion still books.

### 8. §D7: `reject_match` keeps exactly one reason

The prompt now names only *the proposed match's own note names a different merchant/counterparty*,
and says explicitly that the day-gap, currency, account-pair and no-drift reasons are enforced
mechanically **before a proposal exists**, so a match the model can see has already passed them and
re-judging it can only be wrong. The op vocabulary is UNCHANGED — op names are persisted data
(`lucius_ops` on statement records) and renaming one would need an alias.

## Consequences

### Positive
- The three mechanical `reject_match` reasons become unreachable: a whole class of per-row LLM
  arithmetic is gone, cached per source, and auditable as a stamped policy rather than re-derived
  prose.
- The suppression diff makes the tightening failure mode *visible by construction* — the one thing
  the original design would have shipped silently.
- Commit's policy is server-re-derived and hash-checked, so the reviewed preview is the one that
  books, across all four layers rather than just the source's notes.

### Negative — named, not papered over
- **A statement previewed before this contract, under instructions, is REFUSED at commit** until
  re-previewed. That is requirement #6 as written and the repo's no-backward-compat-shim rule; the
  error says exactly what to do.
- **A source that cannot be fingerprinted still pays once per FILE** (csv/xlsx before its profile is
  learned; a PDF with too little digit-free template text) — the record-level cache makes the
  re-preview free, but a new file from that source re-derives, because there is no key to reuse it
  under. R6's correction, partially mitigated rather than solved. Also mitigated by capping the
  extractor's instructions slice at 4k — far below `MAX_INSTRUCTIONS_CHARS` (20k) — and by billing
  the call inside `collect_llm_cost` so it appears on the per-import AI-cost line and the
  duplicate-call tripwire sees it.
- **Two `match_batch` passes when a policy is active** (one baseline, one policy) — two ledger pool
  reads per preview instead of one. Pure compute, no LLM, and only on statements that actually carry
  a policy. Judged worth it: the diff IS the mitigation for the duplicate-manufacturing bug.
- **A layer cannot express "loosen".** Writing `currency_strict: false` in skill metadata is a no-op
  rather than an override of a broader layer. Deliberate (see decision 4), documented in
  `docs/DATA_MODEL.md`, and the only way to reach a looser engine is to remove the tightening layer.

### Follow-ups (deliberately out of scope)
- **The frontend.** Another lane owns `ImportModal` right now; this change emits `match_policy`
  (policy, provenance, `note`, `suppressed`) and the per-row `policy_suppressed` flag but renders
  nothing. §D5's "visible and editable" chip list, the raw-JSON disclosure and the owner override are
  unbuilt, and until the chips land the preview reports suppression only in the API payload.
- **An owner override of the resolved policy** (§D5's "the AI drafts; the owner reviews a diff") —
  the storage and the hash already accommodate it; no surface exists yet.
- **A `MatchPolicy` tool surface for Lucius** (RFC Q3) — v1 stays skill-metadata-only, as proposed.
- **Phase 4** cleanups: the skill-metadata layer is built here, but switching
  `uncovered_ledger_rows`' `_covered_ids` onto `_apply_batch_match` (divergence #5) is untouched.

## Agent provenance

Built by the Phase-3 lane after reading the RFC's security block first, per the task's explicit
instruction that the block overrides the original text. Three places where the RFC did not survive
contact with the code, each resolved above rather than improvised around: the cache had to store the
extraction rather than the resolved policy (§D5 implies the latter, which cannot be re-resolved at
commit without the file); §D6's "field-by-field override" needed the boolean carve-out to stay
consistent with the security block's threat model; and §D5's `derive_policy(instructions)` signature
needed an optional `routed_accounts` keyword to satisfy requirement #5's server-side resolution at
all.
