"""
CASH spend with no bucket HOLDS until month-end (ADR
2026-09-10-uncategorized-and-unlinked-cash-spend-holds-until-month-end).

The scenarios below are the ones walked against real YNAB, side by side, and they carry the
OBSERVED figures to the cent — they are the evidence the rule is right, not merely self-consistent.
One shared baseline book, one branch per scenario:

    on-budget cash                     S$12,563.73
    Citi Rewards (credit)              −S$5,217.64
    Groceries    assigned 100.00, activity −5.50 (a covered CARD swipe) → available  94.50
    Transport    assigned  50.00, activity −2.30 (a CASH spend)         → available  47.70
    Citi Rewards' own payment envelope                                  → available   5.50
    Ready to Assign  12,563.73 − (94.50 + 47.70 + 5.50) = S$12,416.03

    E  cash S$15, NO category            → RTA unmoved; Uncategorized holds −15.00
    G  cash S$8,  unlinked "Subscriptions" → RTA unmoved; that category holds −8.00
    F  card S$15, NO category            → RTA unmoved, holding NOTHING (the debt is the card's)
    H  card S$8,  unlinked "Subscriptions" → RTA unmoved, holding NOTHING

E and G are what ADR 2026-09-10 changed; F and H are ADR 2026-09-09's card rule, asserted here
so the fix cannot quietly drag the card side with it. The split is the whole point: a card never
had cash to lose, so "no bucket" is meaningless for it, and its shortfall surfaces as card debt
(the payment row's `underfunded`/`owed`) rather than anywhere in the RTA identity.

Reuses tests/test_rta_balance_identity.py's isolated `env` fixture and row helpers.
"""

from __future__ import annotations

import pytest

from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import planning
from tests.test_rta_balance_identity import _expense
from tests.test_rta_balance_identity import env as _isolated_env

# Re-exported under its own name so pytest resolves the fixture by the module-level binding
# (same reason as tests/test_rta_identity_property.py — see its note on F811).
env = _isolated_env

_FOOD = "cat_13095ba70000"  # linked to the Groceries envelope below
_TRANSPORT = "cat_db5642ab0000"  # linked to the Transport envelope below
_SUBSCRIPTIONS = "cat_f2ff09940000"  # a REAL, labeled category linked to NO envelope
_MONTH = "2026-06"
_NEXT = "2026-07"

_BASELINE_CASH = 12563.73
_BASELINE_CARD = -5217.64
_BASELINE_RTA = 12416.03
_BASELINE_HELD = 147.70  # 94.50 + 47.70 + 5.50

_ACCOUNTS = [
    {
        "id": "bank",
        "name": "Bank",
        "kind": "asset",
        "type": "cash",
        "currency": "SGD",
        # 12,566.03 opening − the 2.30 Transport spend below = 12,563.73 on hand.
        "initial_balance": 12566.03,
        "initial_balance_date": "2026-06-01",
    },
    {
        "id": "cc",
        "name": "Citi Rewards",
        "kind": "liability",
        "type": "credit",
        "currency": "SGD",
        # −5,212.14 opening − the 5.50 swipe below = −5,217.64 owed.
        "initial_balance": -5212.14,
        "initial_balance_date": "2026-06-01",
    },
]

_ENVELOPES = [
    {
        "id": "env_groceries",
        "name": "Groceries",
        "category_ids": [_FOOD],
        "group_id": "others",
        "order": 0,
        "archived": False,
    },
    {
        "id": "env_transport",
        "name": "Transport",
        "category_ids": [_TRANSPORT],
        "group_id": "others",
        "order": 1,
        "archived": False,
    },
]

# The two movements that shape the baseline: a CARD swipe fully covered by Groceries' 100
# (which is what puts 5.50 into the card's payment envelope), and a plain CASH spend on
# Transport. Neither is a no-bucket spend — every scenario adds exactly one of those on top.
_BASELINE_TXNS = [
    _expense("2026-06-05", _FOOD, 5.50, frm="cc"),
    _expense("2026-06-08", _TRANSPORT, 2.30),
]


@pytest.fixture
def book(env):
    """The shared baseline, rebuilt per test — `set_envelopes` replaces the fixture's 1:1
    builtin envelopes with just Groceries + Transport, which is what leaves Subscriptions a
    real category linked to nothing. `set_accounts` runs AFTER it, so the card's payment
    envelope is re-synced on top rather than wiped by the replace.

    The builtin categories are seeded so "Subscriptions" is a REAL, labeled row rather than a
    bare id — the scenario is specifically about a category the user HAS, not a stray key."""
    categories_service.ensure_builtins_seeded()
    env.set_envelopes(_ENVELOPES)
    env.set_accounts(_ACCOUNTS)
    env.set_assignments({_MONTH: {"env_groceries": 100.0, "env_transport": 50.0}})
    env.set_txns(list(_BASELINE_TXNS))
    return env


def _table(month=_MONTH):
    """`budget_month`, with the identity re-closed from its own published terms every time —
    a scenario that got the headline right by publishing a term that doesn't add up is not a
    scenario that passed."""
    table = planning.budget_month(month)
    assert table["ready_to_assign"] == pytest.approx(
        table["on_budget_cash"]
        - table["pre_budget_baseline"]
        - table["held_in_envelopes"]
        - table["held_outside_envelopes"]
        - table["card_spend_not_covered"]
        - table["assigned_in_future_months"],
        abs=0.005,
    )
    return table


def _unsorted_row(table):
    group = next((g for g in table["groups"] if g.get("unsorted")), None)
    return group["categories"][0] if group else None


def _unbudgeted_row(table, key):
    group = next((g for g in table["groups"] if g.get("unbudgeted")), None)
    if not group:
        return None
    return next((c for c in group["categories"] if c["key"] == key), None)


def test_the_baseline_book_reads_exactly_as_observed(book):
    """Every scenario below is a delta on these figures, so they are pinned first — a baseline
    that drifted would turn each assertion into a test of the wrong book."""
    table = _table()
    assert table["on_budget_cash"] == _BASELINE_CASH
    assert table["held_in_envelopes"] == _BASELINE_HELD
    assert table["held_outside_envelopes"] == 0.0
    assert table["card_spend_not_covered"] == 0.0
    assert table["ready_to_assign"] == _BASELINE_RTA
    rows = {c["key"]: c for g in table["groups"] for c in g["categories"]}
    assert rows["env_groceries"]["available"] == 94.50
    assert rows["env_transport"]["available"] == 47.70
    card = next(a for a in planning.svc.accounts.all() if a["id"] == "cc")
    assert rows[card["payment_envelope_id"]]["available"] == 5.50


# ── E / G: CASH with no bucket — HELD, not drained ──────────────────────────────────────────


def test_scenario_e_uncategorized_cash_spend_holds_and_leaves_rta_unmoved(book):
    book.set_txns(
        [
            *_BASELINE_TXNS,
            {
                "kind": "expense",
                "date": "2026-06-15",
                "category": None,
                "from_account_id": "bank",
                "from_amount": 15.0,
                "from_currency": "SGD",
            },
        ]
    )
    table = _table()
    assert table["on_budget_cash"] == 12548.73  # the cash really did leave
    assert table["held_outside_envelopes"] == -15.00  # ...and the Uncategorized bucket holds it
    assert table["ready_to_assign"] == _BASELINE_RTA  # so Ready to Assign does NOT move
    row = _unsorted_row(table)
    assert row["activity"] == -15.00
    assert row["available"] == -15.00  # a real figure now, not the old "—" stub


def test_scenario_g_unlinked_category_cash_spend_holds_and_leaves_rta_unmoved(book):
    book.set_txns([*_BASELINE_TXNS, _expense("2026-06-15", _SUBSCRIPTIONS, 8.0)])
    table = _table()
    assert table["on_budget_cash"] == 12555.73
    assert table["held_outside_envelopes"] == -8.00
    assert table["ready_to_assign"] == _BASELINE_RTA
    row = _unbudgeted_row(table, _SUBSCRIPTIONS)
    assert row["label"] == "Subscriptions"  # a REAL category, just linked to no envelope
    assert row["activity"] == -8.00
    assert row["available"] == -8.00


# ── F / H: CARD with no bucket — RTA-neutral, holding NOTHING (ADR 2026-09-09, unchanged) ───


def test_scenario_f_uncategorized_card_spend_stays_rta_neutral_and_holds_nothing(book):
    book.set_txns(
        [
            *_BASELINE_TXNS,
            {
                "kind": "expense",
                "date": "2026-06-15",
                "category": None,
                "from_account_id": "cc",
                "from_amount": 15.0,
                "from_currency": "SGD",
            },
        ]
    )
    table = _table()
    assert table["on_budget_cash"] == _BASELINE_CASH  # a card swipe moves no cash at all
    assert table["held_outside_envelopes"] == 0.0  # so there is nothing to hold
    assert table["ready_to_assign"] == _BASELINE_RTA
    row = _unsorted_row(table)
    assert row["activity"] == -15.00  # still VISIBLE — it just holds nothing
    assert row["available"] == 0.0
    assert _table(_NEXT)["ready_to_assign"] == _BASELINE_RTA  # and never charged at the roll


def test_scenario_h_unlinked_category_card_spend_stays_rta_neutral_and_holds_nothing(book):
    book.set_txns([*_BASELINE_TXNS, _expense("2026-06-15", _SUBSCRIPTIONS, 8.0, frm="cc")])
    table = _table()
    assert table["on_budget_cash"] == _BASELINE_CASH
    assert table["held_outside_envelopes"] == 0.0
    assert table["ready_to_assign"] == _BASELINE_RTA
    row = _unbudgeted_row(table, _SUBSCRIPTIONS)
    assert row["activity"] == -8.00
    assert row["available"] == 0.0
    assert _table(_NEXT)["ready_to_assign"] == _BASELINE_RTA


def test_a_card_and_a_cash_spend_on_the_same_unlinked_category_hold_only_the_cash_half(book):
    """The two rules meeting on ONE key — the shape that would break if the hold read the FULL
    activity map instead of its cash-only twin: the row shows −23.00 of activity but holds only
    the 8.00 that was actually cash."""
    book.set_txns(
        [
            *_BASELINE_TXNS,
            _expense("2026-06-15", _SUBSCRIPTIONS, 8.0),
            _expense("2026-06-16", _SUBSCRIPTIONS, 15.0, frm="cc"),
        ]
    )
    table = _table()
    row = _unbudgeted_row(table, _SUBSCRIPTIONS)
    assert row["activity"] == -23.00
    assert row["available"] == -8.00
    assert table["held_outside_envelopes"] == -8.00
    assert table["ready_to_assign"] == _BASELINE_RTA


# ── The roll: hold until month-end, THEN charge if still unresolved ─────────────────────────


def test_an_unresolved_uncategorized_spend_is_charged_to_the_next_month(book):
    """The crux of "hold until month-end, THEN charge". Nobody categorized the 15 before the
    roll, so July starts with the hold reset to 0 and cash still 15 lower — which IS the
    charge, falling straight out of the identity with no separate charge step, exactly as an
    overspent envelope's hole does."""
    book.set_txns(
        [
            *_BASELINE_TXNS,
            {
                "kind": "expense",
                "date": "2026-06-15",
                "category": None,
                "from_account_id": "bank",
                "from_amount": 15.0,
                "from_currency": "SGD",
            },
        ]
    )
    assert _table(_MONTH)["ready_to_assign"] == _BASELINE_RTA
    july = _table(_NEXT)
    assert july["on_budget_cash"] == 12548.73  # cash never came back
    assert july["held_outside_envelopes"] == 0.0  # the hold clamped to 0 at the roll
    assert july["ready_to_assign"] == 12401.03  # == 12,416.03 − 15.00, charged once
    assert _table("2026-08")["ready_to_assign"] == 12401.03  # and never again


def test_categorizing_before_month_end_evaporates_the_hold_and_charges_nothing(book):
    """The same 15, given a home before the roll: Groceries absorbs it (94.50 − 15 = 79.50), the
    Uncategorized bucket disappears entirely, and July is charged nothing. The mirror of covering an
    overspent envelope."""
    book.set_txns([*_BASELINE_TXNS, _expense("2026-06-15", _FOOD, 15.0)])
    table = _table()
    assert table["held_outside_envelopes"] == 0.0
    assert _unsorted_row(table) is None
    rows = {c["key"]: c for g in table["groups"] for c in g["categories"]}
    assert rows["env_groceries"]["available"] == 79.50
    assert table["held_in_envelopes"] == 132.70  # 79.50 + 47.70 + 5.50
    assert table["ready_to_assign"] == _BASELINE_RTA  # unmoved, as it was while held
    assert _table(_NEXT)["ready_to_assign"] == _BASELINE_RTA  # nothing to charge at the roll


def test_linking_the_category_to_an_envelope_before_month_end_also_evaporates_the_hold(book):
    """The other resolution: leave the transaction alone and give its CATEGORY an envelope. The
    8.00 stops being a no-bucket hold and becomes that envelope's own overspend — held by the
    row instead of by the bucket, so Ready to Assign still does not move."""
    book.set_txns([*_BASELINE_TXNS, _expense("2026-06-15", _SUBSCRIPTIONS, 8.0)])
    assert _table()["held_outside_envelopes"] == -8.00
    envelopes_service.envelope_store.replace_all(
        [
            *_ENVELOPES,
            {
                "id": "env_subs",
                "name": "Subscriptions",
                "category_ids": [_SUBSCRIPTIONS],
                "group_id": "others",
                "order": 2,
                "archived": False,
            },
        ]
    )
    planning.sync_card_payment_envelopes()
    table = _table()
    assert table["held_outside_envelopes"] == 0.0
    assert _unbudgeted_row(table, _SUBSCRIPTIONS) is None
    rows = {c["key"]: c for g in table["groups"] for c in g["categories"]}
    assert rows["env_subs"]["available"] == -8.00  # the envelope holds it now
    assert table["ready_to_assign"] == _BASELINE_RTA


# ── Deleting an overspent envelope hands its hole to its now-unlinked categories ────────────


def test_deleting_an_overspent_envelope_does_not_charge_ready_to_assign(book):
    """Deleting detaches every member, so an overspent envelope's hole does not die with the
    row — it moves from `held_in_envelopes` into `held_outside_envelopes`, where the now-unlinked
    category keeps holding it under the same clamped carry (ADR 2026-09-10). Ready to Assign is
    NOT charged now, and the roll charges exactly what it would have charged anyway.

    Pinned because the delete-confirmation dialog, the post-delete toast and `delete_envelope`'s
    own AI tool description all state this to the user before a destructive click. Until
    2026-09-10 they said the opposite, correctly, and this is the change that made them wrong."""
    book.set_txns([*_BASELINE_TXNS, _expense("2026-06-15", _TRANSPORT, 200.0)])
    before = _table()
    assert before["on_budget_cash"] == 12363.73
    assert before["held_in_envelopes"] == -52.30  # 94.50 + (−152.30) + 5.50
    assert before["ready_to_assign"] == _BASELINE_RTA  # the envelope is holding its own hole
    without_delete_next_month = _table(_NEXT)["ready_to_assign"]

    envelopes_service.delete_envelope("env_transport")
    after = _table()
    assert after["held_in_envelopes"] == 100.00  # 94.50 Groceries + 5.50 card payment
    assert after["held_outside_envelopes"] == -202.30  # the hole simply changed hands
    # +50, and every cent of it is the envelope's zeroed assignment coming back — NOT the hole
    # being charged, which is what the old copy claimed.
    assert after["ready_to_assign"] == 12466.03
    # ...and the roll still charges the same amount it would have without the delete.
    assert _table(_NEXT)["ready_to_assign"] == without_delete_next_month == 12263.73


def test_deleting_an_envelope_whose_hole_is_pure_overspend_leaves_rta_untouched(book):
    """The same rule with nothing assigned to muddy it: the envelope is red purely from
    spending, so deleting it moves Ready to Assign by exactly zero."""
    book.set_assignments({_MONTH: {"env_groceries": 100.0}})
    book.set_txns([*_BASELINE_TXNS, _expense("2026-06-15", _TRANSPORT, 60.0)])
    before = _table()
    assert before["held_in_envelopes"] == 37.70  # 94.50 + (−62.30) + 5.50
    assert before["ready_to_assign"] == 12466.03

    envelopes_service.delete_envelope("env_transport")
    after = _table()
    assert after["held_in_envelopes"] == 100.00
    assert after["held_outside_envelopes"] == -62.30
    assert after["ready_to_assign"] == 12466.03  # unmoved, to the cent
