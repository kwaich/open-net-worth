---
number: 2026-07-09
title: YNAB engine mirrors YNAB credit-card handling (payment envelopes)
status: Accepted
date: 2026-07-09
deciders: orchestrating Claude agent + repo owner (in-session)
---

# YNAB engine mirrors YNAB credit-card handling (payment envelopes)

> **Update 2026-07-10 — the two-mode flag was REMOVED; payment envelopes are now the ONLY
> credit-card engine.** This ADR originally shipped `payment_envelope` as an opt-in mode alongside
> the default "signed" mode (card debt nets against cash). After the payment-envelope engine was
> verified **YNAB-exact** — reproducing a real YNAB book to the dollar across RTA, per-card
> Available, Underfunded, and the payment-neutral-on-pay identity (built the exact scenario from
> owner-provided YNAB screenshots) — the "signed" mode, the `credit_card_mode` flag, the
> `is_cash_account` branch, and the `migrate_credit_card_mode.py` flip script were all deleted.
> `is_cash_account` now excludes payment-tracked cards unconditionally; boot syncs a payment
> envelope per on-budget card. The v2 display-*clamp* on `available_for_payment` was ALSO reverted
> the same day — it diverged from YNAB (which shows the honest negative + "cover this
> overspending"); the raw signed Available and `underfunded = owed − available` are YNAB-faithful.
> Everything below is the original two-mode decision, kept for the record.

> **Amendment retired 2026-09-09** — ADR
> `2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category` restores
> `_CARD_UNCOVERED_KEY` and deletes `unfunded_card_debt`; the 2026-09-08 note below is
> historical, describing a state that no longer exists.

> **Amended 2026-09-08 — ADR `2026-09-08-ready-to-assign-is-unassigned-cash`.** The
> coverage-capped v2 uncovered-remainder term, `_CARD_UNCOVERED_KEY`, is GONE — it existed only
> to keep an uncovered card spend RTA-neutral under the old identity's `reserved` sum, and that
> identity is retired. Unfunded card debt (uncovered spend AND standing pre-existing debt alike)
> is now netted CONTINUOUSLY as one term in the RTA identity itself
> (`unfunded_card_debt = Σ max(card_debt − funded, 0)`), so it is subtracted at all times rather
> than patched in per-month. **Payment envelopes are UNCHANGED as the funding mechanism** — the
> auto-move fold, the coverage cap (`min(spend, headroom)`), `available_for_payment`, and
> `underfunded` all still work exactly as described below; only the piece of RTA math that used
> to compensate for an uncovered spend moved out of this ADR's territory and into the identity.

## Context

ADR `2026-07-02-balance-based-rta.md` chose formulation **(B)** for the single budget engine:
credit cards sit in the **signed on-budget cash side** with **no payment-envelope machinery**, so
standing card debt reduces Ready-to-Assign one-for-one. It was chosen deliberately for *less
machinery* (the owner's ask at the time).

Two forces have since changed the calculus:

1. **Pluggable budget engines are the program of record.** `docs/launch/PLATFORM_MASTER_PLAN.md`
   rules the budget paradigm pluggable — the envelope engine is "engine #1", 50/30/20 and
   simple-caps are planned alternates over a defined engine interface (activity fold + assignment
   model + health metrics + Plan render contract), and an explicit exit criterion is **"a YNAB
   refugee imports and sees their budget."**
2. **The ledger just became YNAB-faithful on the opening balance** (ledger-tracking-start Phase 2:
   Starting Balance is now a real ledger transaction, ADR
   `2026-07-09-starting-balance-as-ledger-transaction.md`).

That exposes a half-match: the envelope engine is "YNAB-*inspired*" but **not YNAB-*faithful* on
credit cards**, and a migrant feels it immediately — there is **no "Credit Card Payments" category
group** (a YNAB hallmark), and **card debt reduces their RTA** (YNAB never does this; the cash stays
yours, the debt is *earmarked* in the payment envelope). Faithful starting balances + unfaithful
cards is exactly the kind of mismatch that erodes trust with the audience we most want.

Crucially, credit-card treatment is a **budget-engine** concern (it lives in the activity fold +
RTA identity + render contract the engine interface owns), **not a ledger concern** — cards are just
liability accounts with balances. So it can differ per engine.

## Decision

We will make the **YNAB (envelope) engine** faithfully mirror YNAB's credit-card handling: a
per-card **"Credit Card Payments" payment envelope**, with budgeted money **auto-moved from the
spending category into that payment envelope on each on-budget card spend**, so that **card debt is
NOT subtracted from Ready-to-Assign** (cash stays assignable; the payment envelope holds the
earmark; paying the card draws the payment envelope down). This is a **property of the YNAB engine**
within the pluggable budget-engine seam — **other engines** (50/30/20, a simple/net-worth engine)
define their own credit-card treatment, and **cards-in-signed-cash remains valid** for a simpler
engine.

This **revises the credit-card handling of ADR 2026-07-02 for the YNAB engine specifically**; the
rest of 2026-07-02 (the balance-based RTA identity, on-budget derivation) stands as the ledger-level
fold and for engines that don't use payment envelopes.

## Implementation (2026-07-09, same lane)

Shipped as a **mode flag**, not the pluggable-engine framework the Follow-ups originally scoped
this to (that remains future work — see PLATFORM_MASTER_PLAN P2-B): `profile.credit_card_mode ∈
{"signed" (default), "payment_envelope"}`. The RTA identity `cash − baseline − Σ available` is
unchanged in EITHER mode; the mode only swaps two inputs — `services.cashflow.is_cash_account`
(does an account's balance join the cash sum?) and the envelope universe `sync_card_payment_envelopes`
maintains (a payment envelope is a normal, already-summed row — no new RTA term). `is_on_budget`
stays the untouched mode-agnostic derivation; `is_cash_account`/`is_plan_source` are the two new
facades that diverge (or don't) by mode. Reused the Commitments machinery verbatim as the template
(real envelope via `envelopes.create_envelope`, pinned group, `is_*_synced` guard,
`force_delete_*`, boot-ordered sync) — `envelopes.is_card_payment_synced`/
`force_delete_card_payment_envelope`, `planning.sync_card_payment_envelopes`. Payment-tracked set
is `type=="credit_card"` only (v1), a `frozenset` in `cashflow.py` for trivial widening. The
auto-move fold (`planning._iter_category_contributions`) is additive to the existing per-kind
rules: a payment-tracked card's expense ALSO credits its payment envelope (unconditionally, even
uncategorized — required for RTA-neutrality to hold on the Unsorted bucket too); a transfer whose
to-leg is a payment-tracked card is *always* a payment, pre-empting the categorized-transfer XOR
rule, using the to-leg's own native amount (never `txn_base_amount`). v1 is uncapped by design (the
full spend credits the envelope, not YNAB's coverage-capped min).

**Discovered on the clone verification run (real data, not a synthetic test) — flagged in v1 as a
genuine limitation, RECLASSIFIED below (v2) as correct, intended behavior:** `kind=adjustment`
legs are invisible to the auto-move fold (adjustments never carry a category, by the pre-existing
"adjustment never touches an envelope" invariant this ADR did not revisit). Since
ledger-tracking-start Phase 2 made every account's **opening balance** a real `kind=adjustment`
ledger transaction (`external_id="opening:<id>"`), every card that had pre-existing debt when
tracking started has a payment-envelope Available that diverges from its true current balance —
the opening debt (and any later manual reconciliation, also an adjustment) never enters the
envelope's recursive walk while ordinary spend/payment activity on top of it does. Measured on the
owner's real book (4 cards) under **v1** (uncapped): the migration script's own dual check
(projected simple balance sum vs. the actual engine-computed RTA jump) caught a ~SGD 350 aggregate
divergence; one card (Meridian Bank Visa Infinite) alone showed an Available of −650.00 against a true
current balance of only −150.00 — an ~SGD 1,100 individual-card misstatement, with **no dedicated
signal telling the owner why**. **v2 (below) makes this the rule 4 "starting debt" case, exactly as
designed** — the divergence still exists (an adjustment still never touches an envelope) but is no
longer a silent misstatement: `underfunded` (`_payment_row_fields`) is computed from the card's
own REAL account balance (which DOES include every adjustment), not from the envelope's own
recursive walk, so it always reports the true gap regardless of source. Re-measured on the SAME
owner's book under v2 (clone-verified, not applied live): Meridian Bank Visa Infinite now shows
`available_for_payment = −3,500.00`, `underfunded = 7,200.00` against a true balance of −150.00 —
a LARGER divergence than v1's, but now fully EXPLAINED and SURFACED (a mix of the starting-balance
adjustment plus, now that credits are coverage-capped, months of card spend that was never backed
by an actual assign) rather than hidden inside an opaque Available number.

## Consequences

### Positive
- YNAB migrants land on familiar behavior: the Credit Card Payments group, and RTA that doesn't drop
  because of card debt — directly serving the "YNAB refugee" adoption goal.
- Adopts the mental model many find more correct: your cash is still yours; the debt is earmarked,
  not vanished.
- The pluggable seam keeps every other paradigm free to differ — no model is forced globally.

### Negative
- **Significantly more machinery** than (B): the payment-envelope lifecycle, the auto-move on card
  spend, and the "cover overspending" flow — the exact complexity (B) was chosen to avoid.
- **Visible change to the existing book's RTA**: standing card debt stops reducing RTA, so the
  owner's current RTA shifts (offset by payment-envelope reservations) — needs a data migration for
  existing card debt and owner sign-off on the number change.
- Revisits a deliberately-minimal earlier decision; the hybrid "cards in cash + payment envelope"
  remains forbidden (it double-counts — see 2026-07-02).

### Follow-ups
- **DONE (this lane, mode-flag shipped, opt-in only):** the mode + auto-move fold + sync +
  `backend/scripts/migrate_credit_card_mode.py`; `CLAUDE.md` updated.
- **DONE (v2, same ADR, 2026-07-09, later same day):** capped the auto-move credit at the
  spending envelope's own coverage (YNAB's `min()`), replacing the deliberately uncapped v1 — see
  "Implementation v2" below. This ALSO resolves the adjustment-leg blind spot's follow-up
  decision: rather than folding adjustment legs into the envelope fold (rejected — an adjustment
  is a balance ASSERTION, not a budgetable event, and folding it would need its own coverage
  logic), `underfunded` reads the card's real account balance directly, independent of the
  envelope walk. Still open:
- Fold this mode flag into the **real pluggable-engine seam** (PLATFORM_MASTER_PLAN P2-B / §8.3)
  once that framework exists — today it's a flag read by the one YNAB engine, not yet a
  per-engine interface method.
- Wire the Credit Card Payments group (including the new `available_for_payment`/`underfunded`
  fields) into the Plan tab render contract (frontend — out of scope for this backend-only lane).

## Implementation v2 — coverage-capped auto-move (2026-07-09, later same day)

Replaces v1's uncapped credit with YNAB's real `min()` rule, per the owner's locked spec (both
YNAB's official docs and the owner's own empirical tests agree): on a categorized card spend of
`X` against a spending envelope whose Available *before the spend* is `A`, the amount MOVED into
the card's payment envelope is `min(X, max(0, A))` — never the full `X`. The spending envelope
still drains by the FULL `X` (unchanged; its displayed Available goes negative for the uncovered
part, "credit overspending"); the uncovered remainder (`X − moved`) does **not** fund the payment
envelope at all.

**Where it's resolved.** `services/planning.py`: `_iter_category_contributions` collects every
payment-tracked-card expense as a `(spend_env_id, card_env_id, amount, txn, split)` candidate
(`spend_env_id` is `None` when the category is unlinked/blank — no pool, no coverage, ever) instead
of crediting inline; `_capped_card_credits` resolves them in one batch AFTER the main pass, grouped
by `spend_env_id` (the coverage POOL — a merged N>1 envelope shares one pool across every member
category) but credited to each spend's own `card_env_id` (two different cards spending against the
*same* spending envelope in the same month share headroom but credit different payment rows).
Headroom = `max(0, available(prev_month)) + assigned(month) + cash-only activity(month)` — the
envelope's Available *before* any of this month's card spending against it; "cash-only" excludes
the card-spend drain lines themselves (tracked separately) so the baseline isn't circular.

**Month-aggregate, not per-transaction-chronological** — and this is the one place a shortcut
needed proving, not just asserting: for spends `s1..sn` summing to `S` against one envelope with
headroom `A`, sequential coverage (each transaction draws down the SAME running headroom) yields a
TOTAL moved of `min(S, max(0,A))` **regardless of processing order** — coverage is monotonic, so
reordering only changes which single transaction (or which card, when two share a pool) absorbs a
partial-coverage boundary, never the total. The total is therefore provably order-invariant against
the RTA identity; only the *per-transaction detail view* (`activity_transactions`) needs a
deterministic order, so candidates are still walked in `(date, created_at, id)` order for a stable,
reproducible breakdown — chosen over a genuine chronological interleave of cash-and-card activity
(strictly more YNAB-faithful, but unneeded: no spec'd test case, and no plausible real scenario
this repo's own invariants allow, requires it) for materially less complexity.

**The bug this design had to avoid (found and fixed mid-implementation, not in the original
spec):** naively "just don't credit the payment envelope for the uncovered part" silently
**inflated** RTA — an uncovered card spend still drains the spending envelope's own Available in
full (a real row, fully counted in `reserved`), but since the card itself never touches cash under
this mode, nothing offset that drop, and `RTA = cash − baseline − reserved` INCREASED by the
uncovered amount (verified empirically: a $20 uncovered spend on an otherwise-empty book moved RTA
from 1000.00 to 1020.00). Unlike a cash overspend — where the cash leg's own drop and the
envelope's own drop are the SAME leg, at the SAME amount, and net to exactly zero in-month — a
card spend under `payment_envelope` mode has no such cash-side counterpart. The fix: a dedicated,
non-row, internal-only RTA-identity term, `_CARD_UNCOVERED_KEY` (never a real category or envelope
id, excluded from `_unbudgeted_lines`, never rendered), added to `reserved` alongside the
blank-category Unsorted bucket, carrying exactly `Σ(spend − moved)` for the month. Net effect: an
uncovered card spend is **RTA-neutral**, this month and every month after (the term is recomputed
fresh per month, never carried forward) — generalizing rule 4's "pre-existing/starting card debt
does NOT reduce RTA" to ANY unfunded card debt, spend or starting balance alike. The real gap is
surfaced instead via the payment row's own `underfunded` figure, and only actually moves RTA when
real cash pays it down (a transfer) or an assign retroactively covers it.

**New `budget_month` fields** (payment-envelope rows only, `is_payment_envelope=True`; `None` on
every other row): `available_for_payment` (the row's own raw, signed Available — rule 3, never
clamped: paying with unbudgeted money legitimately drives it negative) and `underfunded =
max(0, owed − available_for_payment)` where `owed = max(0, −account_balance_as_of(card, month))` —
the card's TRUE current debt, read directly off its own ledger balance (which correctly includes
every adjustment leg, unlike the envelope walk). `_payment_row_fields`/`_account_balance_base_as_of`
in `services/planning.py`.

**Verification (clone only, owner's live data never flipped):** full backend suite green
(8,817 passed pre-existing minus one unrelated concurrent-session failure in an unrelated AI tool),
`make lint`'s ruff + import-linter clean on the touched files, 39 dedicated tests in
`tests/test_credit_card_payment_envelope.py` (covered/overspend/partial-coverage/pay-unbudgeted/
starting-debt/cover-then-move/multi-currency/fold-parity), `backend/scripts/
migrate_credit_card_mode.py --apply` rehearsed against a `/tmp` clone of the owner's real data —
net worth unchanged, `credit_card_mode` on the LIVE data dir confirmed still unset (`signed`)
throughout.

## Amendment 2026-09-28 — a categorized refund on a card drains its payment envelope

Found by a side-by-side run of a real book in YNAB and ONW (same starting balances,
transactions and assignments): every spending envelope matched to the cent, but Ready to Assign
was lower in ONW by exactly one refund credited back to a card under a spending category. YNAB
treats that refund as negative card spending: the category gets it back AND the card's payment
category gives it up (the debt it was set aside for shrank). ONW gave it to the category but left
the payment envelope's coverage untouched, so the same money was held twice.

**Decision:** an `income` row landing on a live payment-tracked card under a LINKED spending
category yields a drain of the same amount on that card's payment envelope, beside the existing
category credit (`_iter_category_contributions`, income branch). The refund still widens the
category's coverage headroom, so a same-month spend + refund nets exactly like YNAB's net card
spend. Unchanged: an uncategorized/inflow-category refund (never touches an envelope), an
UNLINKED category's refund (no row, so no offset needed), and a card with no live payment
envelope. Pinned by
`tests/test_credit_card_payment_envelope.py::test_categorized_card_refund_drains_the_payment_envelope_like_ynab`
(400 assigned, 100 cash, 350 card, a 20 refund → category −30, payment 300, RTA unmoved by the
refund); it fails on the pre-fix code at 320.

Still unverified against YNAB: when a category is overspent across several cards, WHICH card's
payment row carries the shortfall (ONW: the chronologically last spends; the parity run's YNAB
rows shared one date, so it could not tell). Totals agree either way.

### Amendment 2026-09-28 — Auto-Assign → Underfunded funds each card's shortfall, last

Side by side with YNAB on the same book, YNAB's Auto-Assign → Underfunded proposed exactly the
targets plus every card payment row's underfunded amount. ONW's showed that same total on the
button but funded only the targets. Both the Budget tab (`buildClientAutoSteps`) and the server
mode (`_auto_underfunded` → `_auto_card_shortfalls`) now add each card's
`underfunded = max(0, owed − max(available, 0))` after the target steps, capped by what Ready to
Assign has left.

This narrows "payment envelopes never take a bulk step" to the one mode where the step is
exact. A target step on a row with Available ≥ 0 moves no coverage (coverage is
`min(card spend, headroom)` and Available is `headroom − card spend`, so that row's swipes are
already fully covered), which keeps the card figures true after the target steps land. When a
server preview funds an OVERSPENT row, coverage can move into a card, so that preview proposes
no card steps at all: the 2026-09-08 double-fund stays impossible, and preview == end state.
The Budget tab's Underfunded only ever funds `underfunded` rows (Available ≥ 0), so the case
cannot arise there. Cover overspending and the resets still never touch a card row.

## Alternatives considered
- **Keep cards-in-signed-cash (B) globally** — rejected: half-matches YNAB (faithful opening,
  unfaithful cards), the precise trust-eroding mismatch for migrants.
- **Force payment envelopes on ALL engines** — rejected: over-constrains simpler paradigms (a
  50/30/20 engine doesn't want CC payment envelopes); defeats the point of pluggable engines.
- **Hybrid: cards in cash AND a payment envelope** — rejected: double-counts the debt (a budgeted
  $50 card spend wrongly drops RTA by $50), established in ADR 2026-07-02.

## Agent provenance

- **Decided by:** orchestrating Claude agent + repo owner (in-session), during the ledger-tracking-start work.
- **Lane / branch:** `feature/ledger-tracking-start`.
- **Alternatives weighed:** cards-in-cash global (B) · payment-envelopes for the YNAB engine (A) · all-engines payment envelopes · hybrid (double-counts).
- **Driving artifacts:** ADR `2026-07-02-balance-based-rta.md`; `docs/launch/PLATFORM_MASTER_PLAN.md` (pluggable budget engines + "YNAB refugee" exit criterion); ADR `2026-07-09-starting-balance-as-ledger-transaction.md`.
- **Why this way (one line):** to be a faithful YNAB alternative for migrants, the YNAB engine's credit-card handling must match YNAB — and the pluggable-engine seam lets it do so without imposing that machinery on other paradigms.
