# Commitment funding is lens-independent, answered in three tiers

**Date**: 2026-08-23
**Status**: **AMENDED same day — the decision below was NOT implemented, and must not be.**
See "Amendment" immediately below before reading anything else.
**Deciders**: owner (directive), Claude (agent — decision delegated explicitly, owner AFK)

---

## Amendment (2026-08-23, before any code was written)

**Nothing in the "Decision" section below was built, and it should not be.** The decision rested on
a premise about this codebase that turned out to be false. It is preserved unedited (append-only)
because the reasoning error is itself worth recording.

**What was verified against the code, after the decision was written:**

1. **Per-commitment coverage is ALREADY lens-independent.** `commitments._plan_fidelity_verdict`
   (`:449`) over `_plan_fidelity_events` (`:422`) computes `at_risk`/`shortfall_base`/
   `earliest_shortfall_date` from *only* that commitment's own unpaid installments (outflows on
   `due_date`) and its own `funding_plan` entries (inflows on `available_date`), starting at $0 and
   deliberately excluding household cash. It never reads `envelope_id`, `home_engine_id`, or
   anything engine-shaped. **Proven, not assumed**: `tests/test_commitments_service.py` runs under
   `db_isolated`, which never writes a `budget_engine_state` or `plan_state.budget` row — so
   `test_coverage_covered_when_funded`, `test_coverage_flags_shortfall`, and
   `test_coverage_plan_fidelity_ignores_unrelated_household_debt` all already pass with **zero**
   budget-engine configuration.

2. **`funding_plan` is ALREADY the paradigm-neutral reservation.** Each `FundingSource`
   (`db/commitments_store.py:38-56`, types `cash`/`income`/`asset_sale`/`loan`/`windfall`) carries
   its own `amount` + `currency` + `available_date`, valued by `live_source_value` (`:62`) from its
   own frozen stored amount — never live-priced, never envelope-dependent. Tier 1's "link a
   commitment to a Target" would have duplicated this with a new FK for no gain.

3. **Tier 3 would have violated an existing, deliberate decision.** A per-commitment *household
   cash* projection reintroduces exactly what the plan-fidelity basis excludes on purpose — so an
   unrelated margin balance cannot mask a commitment's own funding gap. `coverageEngine.ts:1-5` and
   ADR `2026-07-22-commitments-tab-redesign-faithful-port` §2 both state the constraint outright:
   the server is the ONE source of truth, **never a second projection engine**. Tier 3 was a second
   projection engine.

**What the real coupling is** (far narrower than this ADR claimed): one client-side accrual figure
in the commitment *detail modal* — `fundingValue.ts`'s `EnvelopeAccrualState`, plus
`utils/commitments.ts:203-297`'s treatment of a `type='income', cadence='monthly'` funding entry as
an envelope-backed set-aside. That is a coupling to the classic zero-based Planning system
specifically, not to "whichever engine is home". It is **inert today** (no `home_engine_id` write
path exists yet) and **already degrades honestly** — `detailFunding.tsx:327-339` omits the numbers
rather than fabricating them when the live figures do not resolve, which is the very pattern this
ADR was reaching for, shipped back in the 2026-07-22 redesign.

**Amended decision: no change. The math is already correct under all three engines and under none.**

**Carried forward as a Phase 2 acceptance item** (the one place this could become a real gap): once
Phase 2 ships a `home_engine_id` write path and a lens-aware Plan tab, re-verify that
"Manage in Budget →" still lands on a screen where a commitment's synced envelope can actually be
assigned to. Better solved with Phase 2's real UI in hand than speculatively now.

**The reasoning error worth remembering**: the original decision was written from a *design
document's* description of the coupling, before reading `services/commitments.py`. The
lens-independence it set out to build already existed. Read the implementation before designing its
replacement.

---
**Related**: `.claude/rfcs/2026-08-22-pluggable-budget-engines.md` (Decision G, superseded in part),
ADR `2026-07-05-envelope-id-native`, `docs/PLANNING_MODEL.md`

## Context

Commitment funding coverage is currently bound to the envelope model: `fundingValue.ts:139`
fetches `/api/planning/month` and matches funding by `envelope_id`; `detailFunding.tsx` renders
"Funds ⟨envelope⟩". That binding is invisible today because there is exactly one budget engine.

The pluggable-budget-engines RFC makes it visible. With three engines — **zero-based** (RTA, covers
everything), **envelope** (pots, *variable spending only*), **50/30/20** (proportions) — a
commitment like rent has no funding source under two of the three:

- **Envelope** deliberately excludes fixed bills. Rent is not a pot; that is the method, not a gap.
- **50/30/20** has proportional buckets, not per-commitment reservations. "Rent is in Needs" is
  context, not coverage.

The RFC's original recommendation was that commitment funding require the zero-based engine to be
*configured* (not *home*), leaning on the owner's decision A (non-home state stays alive).

**The owner rejected that**: *"commitment funding shouldn't require zero based to be configured — we
need to find a way to support for all 3."* That rejection is correct. Requiring a user to configure
a paradigm they do not use, so that an unrelated screen keeps working, is a workaround wearing a
design's clothes. It also fails on a book that has *never* used zero-based.

## Decision

**Commitment funding stops asking a budget engine.** Coverage is answered in three tiers; the
strongest available tier wins, and **tier 3 is always available**, so every commitment always has an
answer under every lens — and under no lens at all.

### Tier 1 — an explicit reservation (strongest, user-authored, paradigm-neutral)

A commitment may own a **sinking fund**: money deliberately set aside for it, independent of every
engine. This reuses the existing `targets` store, which is already paradigm-neutral
(`{target_amount, target_date, linked_account_ids, …}`) and already portable/checkpointed.

This is not scaffolding for multi-lens. It is what a commitment *wants* regardless of budgeting
method — annual insurance, car registration, and quarterly tax bills are sinking funds by nature,
and no budgeting paradigm is required to believe in them.

### Tier 2 — lens enrichment (optional, per-engine)

If the **active** engine has a notion of money set aside for that commitment, it says so on top:

| Engine | What it contributes |
|---|---|
| zero-based | "$1,800 assigned to Rent this month" — the richest answer, identical to today's behavior |
| envelope | a pot's balance **if** the user made one; silence otherwise (bills usually are not pots) |
| 50/30/20 | "Rent sits in Needs, 2% over target" — context, explicitly *not* a reservation |

Mechanically: one **optional** member on the `BudgetEngine` protocol,
`commitment_coverage(commitment, shared, own_state) -> Coverage | None`. An engine that cannot
answer returns `None` and the host falls back — it never fabricates a number, and the screen never
breaks.

### Tier 3 — cash-flow projection (the floor, always available)

*"Rent $1,800 due Sep 1. Projected balance by then: $3,400. Covered."*

Answerable from the ledger alone via the existing `services/cashflow.py` — no engine, no
assignments, no configuration.

**Projection, not cash-on-hand.** "Do you have $1,800 today?" is close to useless for a bill due in
three weeks — of course you do not, you get paid first. The question a user is actually asking is
*"will I be able to pay this?"*, and only a projection answers it.

**But projection degrades honestly.** When the inputs a projection needs are missing or
untrustworthy (no income history, a book younger than one cycle), fall back to cash-on-hand and
**label which one is being shown**. A projection presented as certainty is worse than no projection
— this mirrors the existing invariant that a failed statement-metadata read is *recorded*, never
cached as fact (ADR `2026-08-01-failed-statement-metadata-is-recorded-not-cached-as-fact`).

## Consequences

**Good**

- Commitment funding works identically under all three engines, and under none.
- **It fixes a real gap that exists today**: a commitment with no envelope linked currently has no
  coverage answer at all. Under this design every commitment always has one, and attaching a lens or
  a sinking fund only ever *improves* it. This is a present-tense product improvement, not
  multi-lens plumbing.
- **It unblocks the phase chain.** Phase 2 (home-lens switching) no longer depends on any particular
  engine being configured; Phase 4 (lens-aware dashboards) unblocks behind Phase 2.
- Coverage becomes explainable: the UI can state *which* tier answered, so a number is never
  mysterious.

**Costs / risks**

- Three tiers is more surface than one lookup. Mitigated by making the tier explicit in the response
  rather than silently blending sources — a blended number nobody can decompose is the failure mode.
- `services/cashflow.py`'s projection assumptions now carry more weight. They must be honest about
  their own confidence; the tier-3 fallback rule above is what keeps that from becoming a lie.
- The optional protocol member is the first *optional* capability on `BudgetEngine`. Optional members
  invite drift. It stays optional deliberately: forcing every engine to answer would push
  zero-based semantics back into paradigms that genuinely have no opinion — the exact coupling this
  ADR removes.

**Explicitly rejected**

- *Require zero-based to be configured.* Rejected by the owner. Forces setup of an unused paradigm
  and fails on a book that never used it.
- *Force every engine to answer.* Would make envelope invent a pot for rent and 50/30/20 invent a
  per-commitment reservation — fabricating data to satisfy a contract.
- *Cash-on-hand as the floor.* Answers the wrong question for anything not due today.
