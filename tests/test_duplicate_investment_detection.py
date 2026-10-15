"""An investment cash leg duplicates like any other cash leg (F14e).

Found while auditing the F14d transfer fix (owner, 2026-08-09: "so other cases that we might be
missing?"). Booking one IDENTICAL row twice, per kind, and asking the engine what it sees:

    expense     -> near_duplicate 1.0   (refused at the write choke)
    income      -> near_duplicate 1.0   (refused)
    transfer    -> near_duplicate 1.0   (refused — F14d, the fix that prompted this audit)
    investment  -> NOT DETECTABLE       <-- the same hole, one kind over
    adjustment  -> NOT DETECTABLE       (correct: F20, a Starting Balance is not a movement)

In this book an ``investment`` row is a POSITION trade's CASH LEG and nothing more — 58 of 58 live
rows carry ``ticker: None`` and ``quantity: None``, the instrument detail living in positions/lots
instead. So it is shaped exactly like an expense and duplicates exactly like one: a dividend or a
buy that arrives from both the broker feed and an email receipt is two rows for one movement.

Unlike F14d's transfers, investment gets NO route shortcut. It clears the ORDINARY near-duplicate
bar — same direction, overlapping account, tight amount, similar description — so this file's job
is to prove the treatment is identical to expense, no looser.
"""

from __future__ import annotations

import pytest

from backend.services import matching

ROUTES = frozenset({frozenset(("acc_brokerage", "acc_bank"))})


def _leg(kind: str, description: str, amount: float = 500.0, date: str = "2026-08-08") -> dict:
    return {
        "date": date,
        "kind": kind,
        "from_account_id": "acc_brokerage",
        "from_amount": amount,
        "from_currency": "SGD",
        "description": description,
    }


def _klass(a: dict, b: dict):
    return matching._classify(a, b, None, ROUTES)


def test_the_same_dividend_booked_twice_is_refused() -> None:
    """The live shape: an agent-booked brokerage dividend and an email receipt for the same
    payout."""
    cand = _klass(_leg("investment", "VOO dividend"), _leg("investment", "VOO dividend"))

    assert cand is not None, "investment was invisible to every duplicate class"
    assert cand.klass is matching.MatchClass.NEAR_DUPLICATE
    assert cand.score >= matching.SCORE_STRONG


def test_two_feeds_wording_the_same_buy_differently_still_collide() -> None:
    """Descriptions never match byte-for-byte across feeds — similarity, not equality, is the bar."""
    cand = _klass(
        _leg("investment", "VOO purchase — 3 shares @ USD 684"),
        _leg("investment", "VOO purchase 3 shares @ 684"),
    )

    assert cand is not None
    assert cand.score >= matching.SCORE_STRONG


def test_two_different_trades_that_merely_cost_the_same_are_not_refused() -> None:
    """The reason investment gets no route shortcut. Two unrelated buys costing the same amount
    must not be collapsed — they stay under the auto bar, so the queue asks instead."""
    cand = _klass(
        _leg("investment", "LUNR purchase 202 shares @ 30.19"),
        _leg("investment", "PL purchase 70 shares @ 34.07"),
    )

    assert cand is None or cand.score < matching.SCORE_STRONG


def test_a_materially_different_amount_never_matches() -> None:
    """The amount gate runs before description similarity is ever consulted."""
    assert (
        _klass(_leg("investment", "VOO dividend"), _leg("investment", "VOO dividend", 525.0))
        is None
    )


@pytest.mark.parametrize(
    "description_a,description_b,amount_a,amount_b",
    [
        ("LUNR purchase 202 @ 30.19", "PL purchase 70 @ 34.07", 500.0, 500.0),
        ("VOO dividend", "VOO dividend", 500.0, 501.0),
        ("VOO dividend", "VOO dividend", 500.0, 525.0),
    ],
)
def test_investment_is_treated_EXACTLY_as_expense(
    description_a: str, description_b: str, amount_a: float, amount_b: float
) -> None:
    """The whole safety argument for F14e: this is not a new, looser rule — it is the rule
    expense has always had, applied to a kind that was excluded by omission. Any divergence here
    means investment got its own tolerances, which is what the one-engine ADR forbids."""
    as_expense = _klass(
        _leg("expense", description_a, amount_a), _leg("expense", description_b, amount_b)
    )
    as_investment = _klass(
        _leg("investment", description_a, amount_a), _leg("investment", description_b, amount_b)
    )

    assert (as_expense is None) == (as_investment is None)
    if as_expense is not None and as_investment is not None:
        assert as_expense.klass is as_investment.klass
        assert as_expense.score == as_investment.score


def test_f23_still_holds_an_investment_leg_is_never_a_transfer_leg() -> None:
    """F23 is a DIFFERENT guard and must survive F14e untouched: an FX-coincident amount must not
    pair an investment's cash leg with an inter-account transfer."""
    transfer = {
        "date": "2026-08-08",
        "kind": "transfer",
        "from_account_id": "acc_brokerage",
        "to_account_id": "acc_bank",
        "from_amount": 500.0,
        "to_amount": 500.0,
        "from_currency": "SGD",
        "to_currency": "SGD",
        "description": "VOO",
    }

    assert _klass(_leg("investment", "VOO"), transfer) is None


def test_an_adjustment_remains_undetectable_by_design() -> None:
    """Not every "NOT DETECTABLE" in the audit was a bug. F20 excludes adjustments deliberately —
    a balance correction is not a real-world movement, and the Starting Balance row has its own
    protections. Pinned so a future kind sweep does not "fix" it."""
    assert (
        _klass(_leg("adjustment", "Starting Balance"), _leg("adjustment", "Starting Balance"))
        is None
    )
