"""
Deleting an envelope never LOWERS Ready to Assign — as a property over random books.

The claim is baked into user-facing copy: the delete-confirm dialog ("its categories keep
holding it until month-end, so Ready to Assign doesn't drop now"), the `delete_envelope` AI
tool's description, and `services/envelopes.py`'s own commentary. Until now it rested on two
hand-picked worked examples. Two examples do not police a claim stated as an absolute — the
2026-09-10 review round found the accompanying FORMULA was wrong on a shape neither example
covered (see below), which is exactly how an unpoliced absolute rots.

WHY IT HOLDS. For a month `m`, with `E` the deleted envelope and `C` its member categories:

    ΔRTA(m) = available(m, E) − Σ_{c ∈ C} min(0, cash_activity(m, c))

`E` leaves `_budget_rows()`, so its signed `available` stops being subtracted; each detached
`c` becomes an unlinked category holding its own hole (ADR 2026-09-10). Expanding
`available = max(0, prev_carry) + assigned + activity` and splitting each category's activity
into its cash part and its non-cash part:

    ΔRTA(m) = max(0, prev_carry) + assigned(m, E) + Σ_c max(0, cash_c) + Σ_c non_cash_c

The first three terms are ≥ 0 by construction. Card spend contributes exactly 0 to the fourth
(the spending row's drain, the payment envelope's capped credit and `_CARD_UNCOVERED_KEY` net
to zero before AND after — the delete removes all three together).

THE FORMULA `services/envelopes.py` USED TO STATE — `max(0, prev_carry) + assigned +
max(0, cash_activity)` — collapsed `Σ_c max(0, cash_c)` into `max(0, Σ_c cash_c)`. Those differ
for a MERGED envelope whose members disagree in sign: spend 100 on one member, refund 100 on
another, and the true ΔRTA is +100 (the hole keeps holding while the sibling's refund goes to
RTA) where the formula predicts 0. The DIRECTION survived; the magnitude did not. Verified
against this file's `test_a_merged_envelopes_members_hold_separately`.

THE SHAPE THAT ONCE FAILED THE DIRECTION, now fixed (ADR 2026-09-28-iterative-available-walk-
and-uniform-card-coverage.md — see the per-variant regression tests in test_rta_balance_
identity.py, e.g. `test_archived_card_spend_on_a_linked_category_is_rta_neutral`): `Σ_c
non_cash_c` used to go NEGATIVE when a `credit`-account leg drained a LINKED envelope with
nothing to offset it — an ARCHIVED card's spend, a card-funded categorized transfer, or a
card-funded categorized investment buy, all of which escaped the payment-envelope fold. Every
such outflow is now a card-spend candidate whether or not a live payment envelope resolves for
it, so `_CARD_UNCOVERED_KEY` cancels the drain exactly like a live card's does and `non_cash_c`
is 0 for it, same as any other card spend — Card spend contributes exactly 0 to the fourth term,
UNCONDITIONALLY, with no more excluded shape. Pinned below by
`test_archived_card_spend_is_no_longer_an_exception_to_the_property`, which asserts the delete
causes NO movement at all (the pre-delete number was the only thing ever wrong here), and the
generator's own restriction against minting such a row on a LINKED category
(`_assert_no_deferred_card_shape`) is kept as a belt-and-suspenders check on the shared
generator, not because the property needs an excluded shape any more.

Seeded `random`, reusing tests/test_rta_identity_property.py's `_random_book` wholesale.
"""

from __future__ import annotations

import random

import pytest

from backend.services import envelopes as envelopes_service
from backend.services import planning
from tests.test_rta_balance_identity import _BASE_ACCOUNTS, _expense
from tests.test_rta_balance_identity import env as _isolated_env
from tests.test_rta_identity_property import (
    _ARCHIVED_CARD,
    _CATS,
    _MONTHS,
    _USD_ACCOUNT,
    _random_book,
)

env = _isolated_env

_SEED = 20260910
_BOOKS = 120
_CARD_IDS = frozenset({"cc", "cc_old"})


def _rta_by_month() -> dict[str, tuple[float, float]]:
    """`{month: (wire_rta, engine_rta)}`. BOTH forms, because they can move for different
    reasons: the wire figure additionally subtracts money assigned to LATER months
    (`_display_rta`), and a delete zeroes the envelope's future assignments too."""
    out = {}
    for month in _MONTHS:
        table = planning.budget_month(month)
        out[month] = (
            table["ready_to_assign"],
            planning._ready_to_assign(planning._build_context(month), month),
        )
    return out


def _assert_no_deferred_card_shape(txns, linked):
    """Belt-and-suspenders: the SHARED generator (`_random_book`, reused wholesale from
    test_rta_identity_property.py) must not mint a `credit`-account leg on a LINKED category
    outside the payment-envelope fold — once the one shape that genuinely lowered RTA on delete,
    now fixed (ADR 2026-09-28-iterative-available-walk-and-uniform-card-coverage.md; see the
    module docstring). Every such leg is now a card-spend candidate regardless of whether it
    escapes the live payment-envelope fold, so the property no longer NEEDS this exclusion — kept
    only so a future change to the shared generator is caught here rather than silently exercised
    for the first time by this file's own fuzz run."""
    for t in txns:
        cat = t.get("category") or ""
        if cat not in linked:
            continue
        if t.get("kind") == "transfer" and t.get("from_account_id") in _CARD_IDS:
            raise AssertionError(f"generator minted a card-funded categorized transfer: {t}")
        if t.get("kind") in ("expense", "investment") and t.get("from_account_id") == "cc_old":
            raise AssertionError(f"generator minted an archived-card leg on a linked cat: {t}")


def test_deleting_an_envelope_never_lowers_ready_to_assign(env):
    rng = random.Random(_SEED)
    env.set_accounts([dict(a) for a in _BASE_ACCOUNTS] + [dict(_USD_ACCOUNT), dict(_ARCHIVED_CARD)])
    envelope_rows = [dict(r) for r in envelopes_service.envelope_store.all()]
    env_ids = [envelopes_service.env_of()[c] for c in _CATS]
    seen = {"deleted_red": 0, "deleted_black": 0, "rta_rose": 0, "rta_flat": 0}

    for book in range(_BOOKS):
        # Whole-store reset: the previous iteration HARD-DELETED one of these rows (ADR
        # 2026-09-26-deleting-an-envelope-deletes-it) and zeroed its assignments — there is no
        # restore to put the money back, so the fixture reconstructs the pristine row set
        # directly instead.
        env.set_envelopes([dict(r) for r in envelope_rows])
        txns, assigns, baseline = _random_book(rng, env_ids)
        _assert_no_deferred_card_shape(txns, frozenset(envelopes_service.env_of()))
        env.set_profile(budget_config={"pre_budget_baseline": baseline})
        env.set_assignments(assigns)
        env.set_txns(txns)

        target = rng.choice(env_ids)
        before = _rta_by_month()
        available_before = {
            m: planning._available(planning._build_context(m), m, target) for m in _MONTHS
        }
        # What the delete-confirm dialog would show a caller a moment earlier — read against the
        # SAME live (pre-delete) book the actual delete is about to run against.
        preview_delta = {
            m: planning.envelope_delete_preview(target, m)["delta_rta"] for m in _MONTHS
        }
        envelopes_service.delete_envelope(target)
        after = _rta_by_month()

        for month in _MONTHS:
            for form, i in (("wire", 0), ("engine", 1)):
                delta = after[month][i] - before[month][i]
                assert delta >= -0.01, (
                    f"book={book} month={month} form={form} seed={_SEED}: deleting {target} "
                    f"LOWERED Ready to Assign by {-delta:.2f} "
                    f"({before[month][i]} -> {after[month][i]}, "
                    f"available_before={available_before[month]:.2f})"
                )
            # The preview NEVER OVER-promises — checked against the ENGINE form, which is what
            # `_envelope_delete_delta` is actually derived from (the wire form additionally
            # clamps for money assigned to a LATER month, `_display_rta`'s own rule, which the
            # preview does not model). It is not always EXACT: when the target's own members
            # carry LIVE payment-tracked card spend this envelope's headroom was covering,
            # unlinking drops that coverage to 0, freeing MORE room on the card's OWN payment
            # envelope than this envelope-local formula prices in — a real, ≥0-only gap
            # (`_envelope_delete_delta`'s own docstring), never a shortfall the confirm dialog
            # then fails to deliver.
            engine_delta = after[month][1] - before[month][1]
            assert preview_delta[month] <= engine_delta + 0.01, (
                f"book={book} month={month} seed={_SEED}: preview promised "
                f"{preview_delta[month]:.2f} but the delete only realized {engine_delta:.2f} "
                "— the preview OVER-promised, which must never happen"
            )
            rose = after[month][1] - before[month][1] > 0.005
            seen["rta_rose" if rose else "rta_flat"] += 1
            key = "deleted_red" if available_before[month] < -0.005 else "deleted_black"
            seen[key] += 1

    # The delete must have been exercised on envelopes that were genuinely RED (the half the
    # hold-changes-hands rule is about) as well as ones in the black, and must have actually
    # MOVED Ready to Assign rather than passing on books where nothing was ever assigned.
    assert all(count > _BOOKS // 10 for count in seen.values()), seen


@pytest.fixture
def small(env):
    """1,000 of cash and one archived card, on the fixture's default 1:1 envelopes."""
    env.set_accounts(
        [
            {
                "id": "bank",
                "name": "Bank",
                "kind": "asset",
                "type": "cash",
                "currency": "SGD",
                "initial_balance": 1000.0,
                "initial_balance_date": "2026-06-01",
            },
            dict(_ARCHIVED_CARD),
        ]
    )
    env.set_assignments({})
    env.set_txns([])
    return env


def test_a_merged_envelopes_members_hold_separately(small):
    """The shape that falsified the old FORMULA (the direction is still fine). One envelope,
    two members: 100 spent on one, 100 refunded on the other. Its Activity nets to 0 and its
    available is 0, so `max(0, cash_activity)` predicts ΔRTA = 0 — but the members hold
    SEPARATELY once detached (`_unbudgeted_available_map` clamps per category, deliberately: a
    category refunded into the black must not cancel another's still-open hole), so the hole
    keeps holding while the refund goes to Ready to Assign. ΔRTA = +100."""
    food, transport = _CATS[0], _CATS[1]
    merged = envelopes_service.env_of()[food]
    envelopes_service.merge_envelope(envelopes_service.env_of()[transport], merged)
    small.set_txns(
        [
            _expense("2026-06-15", food, 100.0, frm="bank"),
            {
                "kind": "income",
                "date": "2026-06-20",
                "category": transport,
                "to_account_id": "bank",
                "to_amount": 100.0,
                "to_currency": "SGD",
            },
        ]
    )
    before = planning.budget_month("2026-06")
    assert (before["ready_to_assign"], before["on_budget_cash"]) == (1000.0, 1000.0)
    envelopes_service.delete_envelope(merged)
    after = planning.budget_month("2026-06")
    assert after["held_outside_envelopes"] == -100.0  # the spend's hole, alone
    assert after["ready_to_assign"] == 1100.0  # ...and the refund, in Ready to Assign


def test_archived_card_spend_is_no_longer_an_exception_to_the_property(small):
    """The shape that USED TO be the documented exception (ADR 2026-09-28-iterative-available-
    walk-and-uniform-card-coverage.md fixes it): an ARCHIVED card's spend on a LINKED category
    drains that envelope, but is now matched dollar for dollar by `_CARD_UNCOVERED_KEY` — no live
    payment envelope to auto-move into, exactly like an unfunded LIVE card spend already worked,
    so Ready to Assign reads 1,000 against 1,000 of cash BEFORE the delete, not 1,100. Deleting
    the envelope then causes NO movement at all: the spend was already neutral by cancellation
    (before), and stays neutral by absence once the category is unlinked (after) — the exact
    "card neutrality is uniform" invariant CLAUDE.md states, now true here too."""
    food = _CATS[0]
    envelope = envelopes_service.env_of()[food]
    small.set_txns([_expense("2026-06-15", food, 100.0, frm="cc_old")])
    before = planning.budget_month("2026-06")
    assert (before["ready_to_assign"], before["on_budget_cash"]) == (1000.0, 1000.0)
    assert before["card_spend_not_covered"] == 100.0  # fully uncovered — no live envelope
    envelopes_service.delete_envelope(envelope)
    after = planning.budget_month("2026-06")
    assert (after["ready_to_assign"], after["on_budget_cash"]) == (1000.0, 1000.0)
    assert after["card_spend_not_covered"] == 0.0  # absence-neutral once unlinked


# ── envelope_delete_preview — the confirm dialog's own number (BOARD.md Inbox repro) ──────────
def test_preview_matches_the_undercounting_repro_from_the_launch_board(small):
    """docs/launch/BOARD.md Inbox: assign S$100, spend S$50 cash → the confirmation used to show
    only `available` (S$50 = 100 assigned − 50 spent), but the actual Ready to Assign change is
    +S$100 — the WHOLE assigned amount releases, and only the spend's negative share stays held
    (it just changes hands, from `held_in_envelopes` to `held_outside_envelopes` — never lost,
    never double-counted). `envelope_delete_preview` must show the TRUE figure, not `available`,
    and the figure it promises must be the one the delete actually realizes."""
    food = _CATS[0]
    envelope = envelopes_service.env_of()[food]
    small.set_assignments({"2026-06": {envelope: 100.0}})
    small.set_txns([_expense("2026-06-15", food, 50.0, frm="bank")])
    before = planning.budget_month("2026-06")
    # 950 cash (1000 − the 50 that actually left the bank) − 50 available held in the envelope.
    assert before["ready_to_assign"] == 900.0

    preview = planning.envelope_delete_preview(envelope, "2026-06")
    assert preview == {
        "month": "2026-06",
        "envelope_id": envelope,
        "available": 50.0,  # the OLD, undercounting figure the dialog used to show
        "delta_rta": 100.0,  # the TRUE figure — what the dialog and the DELETE response must show
    }

    envelopes_service.delete_envelope(envelope)
    after = planning.budget_month("2026-06")
    assert after["ready_to_assign"] == 1000.0  # confirms the preview promised exactly right


def test_preview_refuses_the_same_rows_delete_itself_refuses(small):
    """An archived envelope's preview 422s exactly like the delete would — the confirm dialog is
    never offered a number for a row it can't actually delete."""
    from backend.core.errors import ValidationError

    food = _CATS[0]
    envelope = envelopes_service.env_of()[food]
    envelopes_service.envelope_store.update_envelope(envelope, {"archived": True})
    with pytest.raises(ValidationError):
        planning.envelope_delete_preview(envelope, "2026-06")
