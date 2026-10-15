---
title: Card coverage is uniform across every outflow shape, live or archived; the Available walk is iterative, not recursive
status: Accepted
date: 2026-09-28
deciders: implementing agent (senior-engineer lane) + repo owner (bugs confirmed on docs/launch/BOARD.md Inbox, 2026-09-09/2026-09-10)
---

# 2026-09-28. Uniform card coverage + iterative Available walk

## Context

Two confirmed money-correctness bugs in `backend/services/planning.py`, both in the
`_available`/`_capped_card_credits` neighborhood, fixed together in one change.

**Bug 1 — Ready to Assign inflates on card-funded outflows that drain a LINKED envelope.**
CLAUDE.md's Budgeting invariant states card spending is RTA-neutral — "a card never had cash to
lose" — via the coverage-capped auto-move (ADR 2026-07-09) crediting a LIVE payment envelope, or
`_CARD_UNCOVERED_KEY` cancelling the spending row's own drain when uncovered. That mechanism was
gated on `card_pay_env_of.get(account_id)` returning a LIVE payment-envelope id — true only for a
non-archived, on-budget `credit` account with a synced FK. Three shapes never resolved one and so
fell through to a bare `fold()`, an unmatched drain with nothing cancelling it:
1. An ARCHIVED card's expense — `sync_card_payment_envelopes` retires an archived card's payment
   envelope and clears its FK, so `card_pay_env_of` no longer has an entry for it.
2. A card-funded categorized TRANSFER to an off-budget account (e.g. credit card → brokerage) —
   the transfer branch only ever special-cased money ARRIVING at a card (paying it down); money
   LEAVING one for an off-budget destination just took the ordinary categorized-transfer fold.
3. A categorized investment BUY funded from a card — the investment branch never consulted card
   membership at all, unlike the expense and transfer branches.

All three were confirmed and logged (docs/launch/BOARD.md Inbox, 2026-09-10): S$100/S$200/S$250
inflated Ready to Assign by the exact swipe amount on a category still linked to an envelope.
`services/envelopes.py`'s own `delete_envelope` commentary and
`tests/test_delete_envelope_never_lowers_rta.py` had already carved out and pinned this exact
shape as a deliberately-deferred exception to the "delete never lowers RTA" property.

**Bug 2 — the Budget tab 500s with an uncaught RecursionError.** `_available` walked the
month-to-month carry via genuine Python function recursion, one stack frame per month, capped at
`_MAX_WALK_MONTHS = 1200` — itself already past CPython's default recursion limit (1000), so a
`rollover_start` set far in the past (e.g. "1900-01", ~1,500 months before a 2026 book) raised an
uncaught `RecursionError` well before the cap ever mattered, reachable via
`PUT /api/planning/budget-config`. `_capped_card_credits` additionally re-entered `_available` for
a DIFFERENT envelope mid-walk (to resolve a card's coverage cap) at `_depth=0`, stacking more
frames on top of whatever depth the outer walk was already at — so the true worst case was worse
than "however many months," not just large.

## Decision

**Card membership, not live-envelope resolution, decides whether an outflow is card spend.**
`_build_context` now computes `card_ids` — every `type=="credit"` account, archived included
(`is_payment_tracked_card` is already type-only) — alongside the existing `card_pay_env_of`
(`{account_id: LIVE payment_envelope_id}`, unchanged, still empty for an archived/dropped-out
card). Every outflow whose funding leg is in `card_ids` — an expense, a categorized transfer's
on-budget/moving leg, or an investment buy — is now unconditionally a card-spend candidate fed to
`_capped_card_credits`, carrying `card_env_id=None` when no live envelope resolves.
`_capped_card_credits` credits a resolvable `card_env_id` exactly as before (coverage-capped); a
`None` one is 100% uncovered by construction and is deliberately NOT drawn from the shared
headroom pool (`covered` is forced to `0.0` without touching `headroom`), so an uncreditable card
can never consume coverage capacity a live card sharing the same spending envelope could actually
use. `card_ids` is read off `ctx` inside `_iter_category_contributions`
(`ctx["card_ids"] if ctx else frozenset()`) rather than taking its own parameter — an explicit
extra parameter is one more thing a direct caller could forget to thread, which would silently
disable coverage for a LIVE card too (caught during implementation: two existing fold-parity
tests in `tests/test_credit_card_payment_envelope.py` call `_activity_by_category` with a
hand-picked positional-argument subset of `ctx`, and would have silently lost card coverage
entirely under a separate-parameter design).

**`_available`'s walk is iterative, not recursive.** It now collects every not-yet-memoized month
from the target back to the floor (or a cache hit) into a plain list, then folds forward from the
oldest — an explicit list stands in for the call stack, so total work is still O(months walked)
but bounded by memory, never by CPython's recursion ceiling. `_MAX_WALK_MONTHS` is removed
entirely: it no longer serves its original purpose (stack safety) and enforcing it would silently
produce WRONG numbers for a floor with genuine data further back than 1,200 months. A floor set
before any data exists (legal per `docs/PLANNING_MODEL.md`: "a floor BEFORE the first data month
stays legal — that is just 'carry everything'") now walks every empty intervening month
correctly instead of crashing; `_capped_card_credits`'s cross-envelope re-entry into `_available`
is now an ordinary, safe function call with no depth-reset hazard, since there is no longer a
depth concept to reset.

## Consequences

### Positive

Card spending is RTA-neutral unconditionally, matching CLAUDE.md's stated invariant with no
carved-out exception — `tests/test_delete_envelope_never_lowers_rta.py`'s "delete never lowers
RTA" property now holds with NO excluded shape (the deferred-bug test is rewritten to assert the
shape is neutral, not merely "the delete doesn't make it worse"). A `rollover_start` set
arbitrarily far in the past is correct, not just uncapped-but-crashy. Removing a caller-supplied
`card_ids` parameter closes a live footgun (a correct-looking direct call that silently drops
coverage) rather than merely documenting it.

### Negative

`_iter_category_contributions`'s card-spend gate now depends on account TYPE at read time rather
than on the FK snapshot alone, so an account mid-retype (type already flipped, sync not yet run)
resolves card membership from the NEW type immediately rather than the FK's stale view — a
narrower race window than before, not a behavior anyone depended on, but worth naming. A very old
`rollover_start` (e.g. before any real data) now walks every empty month between the floor and
the first transaction on every `_available` call for every envelope — cheap per month (a fixed
small transaction list scan, memoized per month) but not free; no caller has reported this as a
practical cost, and the walk is bounded by calendar months, never by call depth.

### Follow-ups

None identified — both fixes are self-contained to `backend/services/planning.py` and its direct
callers, all of which already thread `ctx`.

## Alternatives considered

- **Keep `card_ids` as an explicit parameter to `_iter_category_contributions`/
  `_activity_by_category`, threaded by every caller** — rejected: discovered mid-implementation
  that this is exactly the class of bug it would reintroduce (a caller that already threads
  `card_pay_env_of` but forgets the new parameter silently loses coverage for LIVE cards too, not
  just archived ones — reproduced by two pre-existing tests in
  `tests/test_credit_card_payment_envelope.py`). Deriving it from `ctx` (already required
  whenever `card_pay_env_of` is non-empty) makes the two facts impossible to desynchronize.
- **Let an archived card's spend keep counting toward `cash_activity_by_key` (the coverage cap's
  shared headroom pool), only add the missing `_CARD_UNCOVERED_KEY` cancellation** — rejected:
  proved by algebra that letting an uncreditable card's drain consume shared headroom (with
  nothing to credit back) would silently starve a LIVE card sharing the same spending envelope of
  coverage it could actually use — the exact bug this change fixes, just moved one level down.
- **Clamp `_available`'s effective floor to the book's first data month regardless of a
  further-back `rollover_start`** — rejected as the primary fix (though it is mathematically
  equivalent for every empty intervening month, so it would have passed the same regression
  test): `docs/PLANNING_MODEL.md` already documents a floor before any data as legal and
  intentional ("carry everything"), and silently re-defining the effective floor would contradict
  the STORED value `budget_month` echoes for display. The iterative rewrite fixes the actual
  defect (Python recursion depth) without changing what the floor means.
- **Keep `_MAX_WALK_MONTHS` as a defensive iteration cap even after going iterative** — rejected:
  an iterative walk has no stack-safety need for it, and enforcing it would produce numerically
  WRONG results for a floor with real data further back than 1,200 months — a correctness bug in
  its own right, even if unlikely to be hit in practice.

## Agent provenance

- **Decided by:** implementing agent (senior-engineer lane), fixing two pre-confirmed bugs from
  `docs/launch/BOARD.md`'s Inbox.
- **Lane / branch:** isolated worktree, branch `worktree-agent-a3195ddd46592ba61`.
- **Alternatives weighed:** see above — explicit vs. `ctx`-derived `card_ids`; whether an
  uncreditable card should still consume shared coverage headroom; floor-clamping vs. iterative
  rewrite; whether to keep a now-pointless iteration cap.
- **Driving artifacts:** `docs/launch/BOARD.md` Inbox entries dated 2026-09-09 (RecursionError)
  and 2026-09-10 (the three card-coverage variants); `tests/test_rta_balance_identity.py`'s new
  `test_archived_card_spend_on_a_linked_category_is_rta_neutral` /
  `test_card_funded_transfer_to_off_budget_account_is_rta_neutral` /
  `test_card_funded_investment_buy_is_rta_neutral` /
  `test_far_past_rollover_start_walks_without_recursion_error`;
  `tests/test_delete_envelope_never_lowers_rta.py`'s rewritten
  `test_archived_card_spend_is_no_longer_an_exception_to_the_property`.
- **Why this way (one line):** card neutrality and the Available walk should both be correct by
  construction — a live-envelope lookup and a Python stack frame are implementation details, not
  the actual invariants ("a card never had cash to lose" and "a floor before any data is legal").
