"""DB-backed CRUD + money-exactness tests for CommitmentsStore (plan 4a, task 3).

CommitmentsStore is the deepest-nested of the four money-in-payload planning stores —
the whole commitment dict (including installments/funding_plan/actions/loan) is stored
as one verbatim payload column, so these tests prove both plain CRUD AND that the
c1234551 extended shape (loan/actions/funding) round-trips through the DB unchanged.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.db.commitments_store import CommitmentsStore
from backend.db.tables import DEFAULT_BOOK_ID, commitments


def test_create_find_and_delete_round_trip(db_engine):
    s = CommitmentsStore(engine=db_engine)
    c = s.create({"name": "Car", "currency": "sgd"})
    assert c["id"] == "car"
    assert s.find_by_id(c["id"])["name"] == "Car"
    assert s.delete(c["id"]) is True
    assert s.find_by_id(c["id"]) is None
    assert s.all() == []


def test_writes_default_book_id(db_engine):
    s = CommitmentsStore(engine=db_engine)
    s.create({"id": "car", "name": "Car"})
    with db_engine.connect() as conn:
        row = conn.execute(select(commitments).where(commitments.c.id == "car")).first()
    assert row.book_id == DEFAULT_BOOK_ID
    assert s.find_by_id("car")["name"] == "Car"  # surface unchanged


def test_commitments_extended_shape_survives_verbatim(db_engine):
    """The c1234551 extended shape (actions/loan/funding) is nested INSIDE payload — it
    round-trips for free, no absorb-time field handling needed."""
    s = CommitmentsStore(engine=db_engine)
    c = s.create(
        {
            "name": "Car",
            "kind": "purchase",
            "loan": {"principal": 1000, "apr": 6.5, "term_months": 24, "payment": 90.5},
            "actions": [{"label": "sign"}],
        }
    )
    back = s.find_by_id(c["id"])
    assert back["loan"]["payment"] == 90.5
    assert back["loan"]["principal"] == 1000.0
    assert back["actions"][0]["label"] == "sign"


def test_money_exactness_across_nested_installments_and_funding(db_engine):
    """Installment/funding amounts live INSIDE the verbatim payload JSON — Python's
    shortest-repr float encoding makes the round-trip exact, no Float-column coercion
    (unlike budget_assignments' promoted decimal-Text column — this store has none)."""
    s = CommitmentsStore(engine=db_engine)
    tricky = 0.1 + 0.2  # 0.30000000000000004 — the classic non-terminating binary case
    c = s.create(
        {
            "name": "Apt",
            "installments": [{"label": "Deposit", "amount": tricky}],
            "funding_plan": [{"type": "cash", "label": "Savings", "amount": tricky}],
        }
    )
    back = s.find_by_id(c["id"])
    assert back["installments"][0]["amount"] == tricky
    assert back["funding_plan"][0]["amount"] == tricky


def test_query_commitments_filters_by_kind_and_status(db_engine):
    """query_commitments was rewritten from self._load_items() to self.all() — this
    proves the filtering behavior is unchanged post-rewrite."""
    s = CommitmentsStore(engine=db_engine)
    s.create({"name": "Apt", "kind": "purchase", "status": "active"})
    s.create({"name": "Meridian Bank loan", "kind": "loan_repayment", "status": "active"})
    s.create({"name": "Old", "kind": "purchase", "status": "completed"})
    assert len(s.query_commitments(kind="purchase")) == 2
    assert len(s.query_commitments(status="active")) == 2
    assert len(s.query_commitments(kind="purchase", status="active")) == 1


def test_nested_writers_still_work_through_the_base_store(db_engine):
    """set_installments/update_installment/add_funding_sources etc. all call
    find_by_id/update_by_id — unchanged domain methods over the new base class."""
    s = CommitmentsStore(engine=db_engine)
    c = s.create({"name": "Apt", "installments": [{"id": "inst-a", "amount": 100}]})
    updated = s.update_installment(c["id"], "inst-a", {"amount": "250", "status": "paid"})
    inst = updated["installments"][0]
    assert inst["amount"] == 250.0 and inst["status"] == "paid"
    appended = s.add_funding_sources(
        c["id"], [{"type": "cash", "label": "Earmark", "amount": 7000}]
    )
    assert len(appended["funding_plan"]) == 1
