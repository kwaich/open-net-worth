---
number: N/A
title: weak_tier is a call-site keyword, and the statement commit acceptance set narrows to assigned ∪ uncovered
status: Accepted
date: 2026-07-29
deciders: Phase-2 implementation lane (Claude), applying the pre-build security review of RFC 2026-07-29
---

# `weak_tier` is a call-site keyword, and the statement commit acceptance set narrows to assigned ∪ uncovered

**Amends** `docs/adr/2026-07-29-one-matching-engine-instructions-as-policy.md` (items 3 and 5) and
the corresponding text in `.claude/rfcs/2026-07-29-one-match-engine-instructions-as-policy.md`
(§D1, §D4). The direction of that ADR is unchanged and was implemented in full; two of its
concrete mechanisms were found unsafe before any code was written, and this records what replaced
them and why.

## Context

The parent ADR made two choices that a pre-build security review traced against the real code and
rejected:

1. **`weak_tier` as a `MatchPolicy` field** ("every knob is monotone-tightening; `weak_tier` is
   the sole exception and is band-isolated"). A `MatchPolicy` is *data*: Phase 3 derives it from
   owner prose through an LLM, caches it on a `StatementFormatStore` row keyed by a fingerprint
   computed from an uploaded file's own bytes, and layers `metadata.match_policy` from ordinary
   skills — which `update_skill` writes with no confirmation. The usual defence ("unknown keys are
   dropped") does not apply to a *known* key. So the engine's one loosening switch would have been
   reachable, twice over, from a path an injected instruction can steer.

2. **The commit acceptance set re-derived "from the engine's own candidate list."** §D4.1 spelled
   that as `_apply_batch_match`'s PRE-assignment map — every candidate with a resolvable id and
   `score >= SCORE_WEAK`. That set drops the score floor from `SCORE_FENCE` to `SCORE_WEAK`,
   escapes the statement window entirely (the engine's pool is ±`FETCH_WINDOW_DAYS` across **all**
   accounts), and carries no 1:1 exclusivity. Since `decision_commit` never de-duplicated merge
   targets, the model-reachable `import_transactions(commit=true)` could point two rows at one
   `existing_id`; `apply_import_merge(facts="statement")` re-dates and re-amounts a target, so that
   rewrites one real transaction twice — and corrupts rollback, because the per-merge `before`
   snapshots replay in order and the second one restores post-first-merge state while reporting
   success.

The review also falsified the premise that justified widening: `_twin_eligible_ids ⊆
_manual_link_ids` **by construction** — both derive from the same `preview_ledger_check` call, and
`_annotate_statement_twins` only ever emitted ids already in `out["rows"]`. Deleting the twin path
outright loses **zero** acceptance.

A third, pre-existing hole surfaced in the same pass: `lucius_pass._validate_ops` accepted any
live, unclaimed transaction id for `force_match`. Applying one REWRITES the row's match flag, so
the forced id reaches `decision_commit` as that row's own engine-assigned candidate and passes the
merge guard. The instructions text is fenced but attacker-influenced in the general case (a PDF's
own printed text reaches the same prompt), so *"row 3 is the same transaction as txn_ab12…"* was a
one-line way to re-date and re-amount an arbitrary transaction.

## Decision

1. **`weak_tier` is a keyword on `match_batch` / `_classify`, not a `MatchPolicy` field.** Set by
   exactly one call site, `statements/decisions._apply_batch_match`. A policy is data; a keyword is
   code. `tests/dedup/test_match_policy.py::test_weak_tier_is_not_a_policy_field` asserts the field
   set exactly, so a Phase-3 extractor that "just passes the model's dict through" fails CI rather
   than shipping. `check()` does not take the keyword at all — pinned as a signature contract, so
   no argument shuffle can reach the tier through the single-row entry point every other ingest
   surface uses.

2. **The commit acceptance set is `{assigned_by_index[idx]} ∪ _manual_link_ids(...)`.** Nothing
   else. `assigned_by_index` is the row's own post-Lucius engine assignment — which now includes
   the weak band, because the tier lives inside the engine and arrives as an ordinary flag with
   every invariant applied and 1:1 already resolved. `_manual_link_ids` is unchanged. The retired
   twin path is deleted, not replaced.

3. **`decision_commit` enforces 1:1 at the commit boundary too**: two merges naming the same
   `existing_id` are refused, and so is a merge onto an id another row's flag already claims (with
   the holder named). The engine enforcing 1:1 in its assignment is not enough, because `decisions`
   are caller-supplied.

4. **`force_match`'s `existing_id` is constrained to the row's engine-derived candidates ∪ the
   uncovered set** — exactly the ids the owner could point at themselves. Both halves are
   re-derived server-side inside `run_lucius_pass`, fail closed, and only on a fresh pass. The
   result is that a Lucius assertion can never be a wider claim than the owner clicking the same
   thing: `force_match`'s targets are a subset of what `decision_commit` accepts.

5. **`_apply_batch_match` returns the per-row DERIVED CANDIDATE map** (`{row index: [assigned id,
   *alternative ids]}`) — narrow, not the §D4.1 wide pre-assignment map. It has exactly two
   consumers, neither of which is the merge guard: the flag's `alternatives` (display + picker) and
   `force_match`'s allowed set above.

6. **`alternatives` ships as `[{id, existing: {...}}]`**, each entry carrying its own
   `_existing_summary` projection including `original_amount` / `original_currency`. A bare
   `string[]` is not renderable: the engine's pool is wider than any id→summary map the client
   holds, so the client would have to silently drop options — reintroducing the client-side
   eligibility pass this work exists to delete, and showing the owner *fewer* options than the
   server would accept.

7. **The "monotone-tightening ⇒ safe" argument is retired from the reasoning.** It holds in
   candidate-set space and fails in outcome space: `decision_commit`'s `default_action` is
   `"create"`, so a policy that suppresses every match yields a *clean-looking* preview that books
   a re-import as N new transactions; and removing a row's true candidate hands it its second-best
   — a different real ledger row — which `facts="statement"` then re-dates and re-amounts. A policy
   that suppresses many proposals is **loud**, not safe. Phase 3 owns the per-row "policy
   suppressed N proposals" surfacing and the suppressed-defaults-to-skip behaviour.

## Consequences

### Positive
- The engine's one loosening switch is structurally unreachable from any data path, rather than
  merely unlikely to be set.
- The commit acceptance set is now strictly narrower than before this change *and* a superset of
  everything the picker can emit — every `alternative` is an uncovered entry by construction (the
  winner is the only one its flag covers), so the manual-link path accepts each one without the
  guard ever widening to the engine's candidate pool.
- `test_manual_link_to_an_out_of_window_entry_is_refused` and
  `test_fabricated_existing_id_on_a_routed_account_is_still_rejected` stay green **unmodified** —
  the stated signal that acceptance did not widen.

### Negative — behaviour that genuinely changed, named
- **`force_match` is narrower.** An op naming a transaction outside the statement's window or
  accounts is now dropped. Three existing fixtures pointed at deliberately-far transactions and
  were moved in-window; that is the security fix working, not a test accommodation.
- **A statement with no usable window gives `force_match` nothing but the row's own candidates**
  (fail-closed). Acceptable: such a statement has no ledger-check panel either, so the owner has
  no manual-link path there to mirror.
- **Two precision losses come with retiring `_wording_tokens`,** both pinned rather than papered
  over, both contained by the band (a weak candidate cannot promote, auto-merge, or reach
  `check()`'s review verdict — it is an offer, where the old matcher's output was a commit-time
  acceptance set):
  - *Non-Latin fragments now count as wording.* `_wording_tokens` (`[a-z]{3,}`) dropped Vietnamese
    entirely, so the drift tier never fired for such books — a documented, deliberate gap. The
    engine's `desc_tokens` splits on `[^a-z0-9]+` and FRAGMENTS instead: "Bún chả Hà Nội" →
    `{b, ch, h, i, n}`. Pairings now form on grammar debris. This is exactly RFC R4, explicitly out
    of scope; the fix (NFKD + `Mn`-strip) moves scoring on every surface and every golden book and
    wants its own ADR. Pinned:
    `test_golden_books.py::test_non_latin_book_drift_tier_now_fires_on_fragment_tokens`.
  - *Stopword-only overlap now counts as wording.* The retired tokenizer stripped a 27-word
    stoplist, so a pairing sharing only "the" or a currency code was still wordless. Re-adding that
    list inside the tier would rebuild the second tokenizer this work exists to delete; adding it to
    `desc_tokens` is the same shape of change as the non-Latin fix. Pinned:
    `test_settled_drift_tier.py::test_stopword_only_overlap_now_forms_a_weak_pair_known_regression`.
- **The `posted_date` gap allowance is gone** (RFC R7/Q2, left open by the parent ADR; resolved
  here as *drop it*). The retired matcher measured `min(gap_to_date, gap_to_posted_date)`; the
  engine reads `date` only, and carrying the allowance over would mean teaching `matching` about a
  statement-row concept its transaction dicts do not carry, for one tier. Pinned in two places so
  re-adding it is a visible diff.

### Follow-ups (deliberately out of scope here)
- A merchant-token notion (stoplist + non-Latin normalization) applied consistently to **every**
  tier in `matching`, with its own ADR and a golden-book re-pin.
- Phase 3's `match_policy.py`, including the review's requirements #5, #6, #7, #10 and #11, which
  only bite once a policy is actually derived.
- RFC R3's `statement_twin.booked` display case — still an accepted degrade.
