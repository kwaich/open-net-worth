---
number: N/A
title: Collapse the statement twin matcher into the match engine, and make owner instructions deterministic engine policy
status: Proposed
date: 2026-07-29
deciders: system-architect lane (Claude) + orchestrating agent; pending repo-owner sign-off
---

# Collapse the statement twin matcher into the match engine, and make owner instructions deterministic engine policy

## Context

The statement-import flow carries **two independently-implemented scoring engines** for the same
underlying question, plus a third re-derivation in the browser:

1. `backend/services/matching.py::match_batch` (via `statements/decisions.py::_apply_batch_match`) —
   statement row → existing ledger transaction. Global 1:1 exclusivity, books-close promotion, four
   code-enforced invariants, per-class day-gap and amount caps. Lucius has authority over it
   (`reject_match` / `force_match`, ADR `2026-07-28-lucius-pass-authority-over-match-engine`).
2. `backend/services/statements/ledger_diff.py::_annotate_statement_twins` → `_rank_twin_pairs` —
   uncovered ledger transaction → statement row. Its **own** 1-day gap cap (measured on
   `min(date, posted_date)`), its **own** 5% drift tolerance, its **own** ASCII-only wording
   tokenizer (`[a-z]{3,}`), its **own** claim assignment over a separate pool. Lucius has **no**
   authority over it — `lucius_pass._MATCH_FLAG_TYPES` covers only system 1's flags.
3. `ImportModal.tsx::weakResolved` — a client-side third pass that re-pairs system 2's output with
   its own eligibility/steal/fallback resolution, plus one narrow patch excluding rows Lucius
   rejected. It exists because nothing reconciles 1 and 2 on the server.

Two facts made this decision necessary now.

**`_rank_twin_pairs` applies no kind gate and none of the engine's invariants.** `_collect_uncovered`
excludes only deleted and `kind == "adjustment"` rows, so an uncovered `kind=transfer` ledger entry
is an eligible "weak match" for an ordinary expense statement row — a pairing `_classify` refuses
outright (F14a, `matching.py:634-641`). That is the shape of the 2026-07-28 live incident, and it was
structurally unreachable by Lucius or by prompt work.

**"Display-only" acquired write authority.** `matching.py:66-69` documents `TWIN_SETTLED_DRIFT_TOL`
as *"DISPLAY-ONLY … never an enforcement decision."* `decisions.py::_twin_eligible_ids` now turns the
twin matcher's pre-assignment eligible set into the server-side acceptance set for a merge at
`decision_commit`. An invariant-free matcher authorizes writes against real ledger rows.

`.claude/rfcs/batch-aware-dedup.md` already recorded this decision once — *"A separate twin matcher
for the ledger-check panel … Rejected/reverted: a parallel matcher with its own tolerances outside
the engine"* — and specified the twin annotation survive only as display text. The code grew back and
then grew teeth, which is the strongest argument for enforcing it structurally rather than by intent.

Separately, owner instructions ("no drift in date", "this source never has drift-matched
duplicates") reach the engine **only** as LLM prose: `lucius_pass.py`'s prompt asks the model to
compute day gaps and percentage tolerances itself, per row, per import, and emit `reject_match`. Three
of that prompt's four enumerated reject reasons are arithmetic or a lookup, not judgment — and this
session's real bugs (fabricated reasoning; reasoning bleeding into an unrelated structural field) came
from exactly that.

## Decision

We will pursue the direction designed in
`.claude/rfcs/2026-07-29-one-match-engine-instructions-as-policy.md`:

1. **One engine.** `matching.py` becomes the sole producer of row↔ledger identity signals for
   statement import. The "which of my ledger entries are unaccounted for" view is derived by
   **inverting** that one assignment (`preview_ledger_check` already builds its covered set from the
   engine's own flags), never by re-scoring.
2. **The settled-drift tier is absorbed as an explicitly banded WEAK class**, not deleted and not
   merged into existing tolerances. It fires only behind an opt-in flag, sits below `SCORE_FENCE`,
   can never promote or auto-merge, and — critically — inherits the kind gate and all four engine
   invariants by living inside `_classify`. Deleting the tier would lose the real settled-drift
   duplicate; widening the engine's global tolerances would move the safety line for every other
   ingest surface at once. The band is the only correct axis.
3. **`ledger_diff.py` becomes a pure diff + money tie-out.** `_rank_twin_pairs`,
   `_annotate_statement_twins`, `_twin_candidates`, `_wording_tokens` and the `statement_twin` /
   `twin_eligible` response keys are deleted. `decisions._twin_eligible_ids` is deleted; the
   commit-time merge acceptance set is re-derived from the **engine's** own candidate list.
4. **The frontend `weakResolved` system is retired**, not preserved as a compatibility layer:
   `matchBand()` reads `band: "weak"` from the server, `twinPick` collapses into the existing
   `matchOverride`, and the picker's options come from a server-supplied `alternatives` list.
5. **Owner instructions become a structured, deterministic `MatchPolicy`** — one bounded,
   schema-validated LLM extraction per (source, instructions-hash), cached on the existing format
   profile beside `instructions`, threaded into the engine as an explicit parameter beside
   `BatchContext`. Every knob is **monotone-tightening** (a policy can never loosen the engine);
   `weak_tier` is the sole exception and is band-isolated.
6. **Layering between the two existing guidance mechanisms is settled: layer, source wins.**
   `MatchPolicy()` defaults < global statement skills < account-scoped statement skills
   (`skills_service.py`, via structured `metadata.match_policy`) < this source's instructions
   (`profiles.py`, via extraction). Field-by-field override, with per-field provenance shown to the
   owner.
7. **Lucius keeps its full op vocabulary; only the prompt narrows.** `reject_match`'s four
   enumerated reasons collapse to one — *the match's own note names a different
   merchant/counterparty* — because the other three are now enforced mechanically before a proposal
   exists. `force_match` is unchanged and remains the only way to assert a match the engine refuses.
   Weak-band proposals are ordinary flags, so Lucius's existing authority extends to the
   reconciliation view with **no new op**.
8. **The commit-time security re-derivation is preserved and strengthened.** `decision_commit`
   continues to re-derive the engine's verdict server-side rather than trusting a caller-supplied
   `existing_id`; it now does so from one engine instead of two, and the resolved policy is read from
   a record stamp (commit never bills an LLM).

## Consequences

### Positive
- The incident class ends structurally: a transfer can no longer be proposed as an expense's
  duplicate, because there is only one classifier and it already refused that.
- Lucius's authority over the reconciliation view arrives for free — one flag surface, one veto.
- "No drift in date" becomes arithmetic the engine performs, not prose an LLM re-derives per row
  per import. The hallucinated-reasoning failure mode has nowhere to live.
- ~170 lines of scoring leave `ledger_diff.py`; the client's three-pass steal/fallback resolver and
  a whole duplicate override concept (`twinPick`) leave the frontend.
- Write authority stops flowing from an invariant-free matcher.
- All duplicate tolerances genuinely live in one file again, as `matching.py`'s own comment claims.

### Negative
- `matching.py` gains a surface-shaped knob (`weak_tier`). Mitigated by naming it for the evidence
  tier, defaulting it off, and pinning every other surface byte-identical — but the tension is real.
- One more cached LLM call per source. Cheap, forever-cached, visible on the AI-cost line — but it is
  another model in the loop, and a mis-derived policy silently *tightens* matching (a missed
  duplicate is quieter than a wrong one). Clamping + visible per-field provenance + owner override
  are the mitigation.
- The `statement_twin.booked` display case ("your ledger already has this from an earlier import")
  cannot be reproduced directly by the unified pass, because L0-duplicate rows are excluded from the
  candidate set. Either an accepted degrade or a second explicit inversion — named as a follow-up,
  not papered over.
- Phase 2 must land backend + frontend as one unit; a split would double-claim entries.
- Retiring the twin matcher silently drops its `posted_date` gap allowance unless deliberately
  re-added.

### Follow-ups
- Build per the RFC's phases; Phase 0 (characterization pins) is blocking.
- `/security-review` (money + external I/O) and `/code-review` before merge; UI Quality Gate
  (design spec → `docs/qa/acceptance/` → `make qa-e2e` → `make ui-gate` → screenshots) for the
  ImportModal change.
- `docs/DATA_MODEL.md`: a "statement match policy" section (layers, precedence, cache keys).
- **Separate ADR required**: the non-Latin tokenization gap. `payee_key` shreds Vietnamese into 1–2
  character fragments and `_wording_tokens` drops it entirely; the NFKD/`Mn`-strip fix changes
  scoring for every surface and every golden book. Explicitly out of scope here.
- Resolve RFC open questions Q1 (stale-policy behavior at commit) and Q2 (`posted_date` in the weak
  tier) with the owner before Phase 3.

## Alternatives considered

- **Keep both engines, extend `_MATCH_FLAG_TYPES` so Lucius can veto twins too** — rejected: it
  appoints an LLM as the reconciler of two disagreeing deterministic systems, the most expensive and
  least reliable place to fix a divergence, and leaves an invariant-free matcher authorizing writes.
- **Delete the twin tier outright** — rejected: loses the genuine settled-drift duplicate (a booked
  estimate vs a settled figure), a real class in an FX-card book.
- **Widen `AMOUNT_REL_TOL` / relax the day-gap caps globally instead of adding a band** — rejected:
  `matching.py` is consumed by `import_anomalies`, `match_review`, `bulk_add_transactions`, connector
  sync and the `FLOOR` choke; it would move all of their safety lines at once.
- **Fold policy extraction into the existing Lucius pass call** — rejected on ordering (the matcher
  runs before the pass per ADR 2026-07-28, and the policy is an *input* to the matcher) and on cache
  lifetime (per-source outlives per-statement).
- **Deterministic keyword/regex extraction of the policy** — rejected: free-form multilingual owner
  prose; a partial regex hit that disagrees with the model is worse than either alone. Safety comes
  from clamping, monotone-tightening and owner-visible provenance instead.
- **Store the policy on the statement record only** — rejected: re-bills per file and loses the
  "type it once, it steers every future import" property `profiles.py` already provides.
- **A new `MatchClass.SETTLED_TWIN`** — rejected: it *is* a near-duplicate; a new class would ripple
  into every `klass`-switching consumer for no semantic gain. Band is the right axis.
- **Keep `weakResolved` as a thin presentation layer over unified data** — rejected: with server
  bands and a server `alternatives` list there is nothing left for it to resolve, and leaving a
  client-side pairing resolver in place is how the divergence regrew last time.

## Agent provenance

- **Decided by:** system-architect lane (Claude, Opus 5), design-only — no implementation.
- **Lane / branch:** `fix/starting-balance-one-place` (design artifacts only; build lands on its own
  branch).
- **Alternatives weighed:** keep-both-and-extend-Lucius · delete-the-tier · widen-global-tolerances ·
  fold-into-the-Lucius-call · regex extraction · new MatchClass · keep `weakResolved` as presentation.
- **Driving artifacts:** `.claude/rfcs/2026-07-29-one-match-engine-instructions-as-policy.md`;
  `.claude/rfcs/batch-aware-dedup.md` (the same decision, previously made and eroded);
  ADR `2026-07-28-lucius-pass-authority-over-match-engine`; the 2026-07-28/29 MoMo import incident.
- **Why this way (one line):** two matchers with different rules cannot be reconciled by prompting
  an LLM at the seam — collapse them into the one engine that already carries the invariants, and
  give the owner's instructions a deterministic parameter instead of a sentence to re-interpret.
