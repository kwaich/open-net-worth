"""PayeeStore on BookPayloadTableStore (plan 4d, task 2) — DB-CRUD round trip plus the
default-category learning / alias-resolution parity that feeds the ledger's categorization
(money-adjacent — CLAUDE.md: payee_id + default_category are consumed by the ledger choke).
"""

from __future__ import annotations

from backend.db.payee_store import PayeeStore
from backend.db.tables import DEFAULT_BOOK_ID


def test_create_round_trip_stamps_book_id(db_engine):
    store = PayeeStore(engine=db_engine)
    payee = store.create("Anthropic", aliases=["anthropic"])
    assert payee["id"].startswith("pay_")
    assert payee["name"] == "Anthropic"
    assert store.find_by_id(payee["id"])["aliases"] == ["anthropic"]

    from sqlalchemy import select

    from backend.db.tables import payees

    with db_engine.connect() as conn:
        row = conn.execute(select(payees).where(payees.c.id == payee["id"])).first()
    assert row.book_id == DEFAULT_BOOK_ID


def test_create_seeds_expense_default_category(db_engine):
    store = PayeeStore(engine=db_engine)
    payee = store.create("Starbucks", default_category="cat_food")
    assert payee["default_categories"] == {"expense": "cat_food"}


# ── Domain parity: default-category learning + alias resolution (money-adjacent) ───────────


def test_payee_default_category_learning_preserved(db_engine):
    """The brief's own named test — set_default_category_for_kind / find_by_alias parity."""
    store = PayeeStore(engine=db_engine)
    payee = store.create("Anthropic", aliases=["anthropic api"])

    # find_by_alias resolves the normalized description back to this payee.
    assert store.find_by_alias("anthropic api")["id"] == payee["id"]
    assert store.find_by_alias("unrelated merchant") is None

    # Learn a per-kind default; other kinds stay untouched.
    updated = store.set_default_category_for_kind(payee["id"], "expense", "cat_software")
    assert updated["default_categories"] == {"expense": "cat_software"}
    updated = store.set_default_category_for_kind(payee["id"], "income", "cat_refund")
    assert updated["default_categories"] == {"expense": "cat_software", "income": "cat_refund"}

    # Clearing one kind (falsy category) never touches the other kind's slot.
    cleared = store.set_default_category_for_kind(payee["id"], "expense", None)
    assert cleared["default_categories"] == {"income": "cat_refund"}


def test_set_default_categories_replaces_whole_map(db_engine):
    store = PayeeStore(engine=db_engine)
    payee = store.create("Grab")
    store.set_default_category_for_kind(payee["id"], "expense", "cat_transport")
    replaced = store.set_default_categories(payee["id"], {"income": "cat_refund"})
    assert replaced["default_categories"] == {"income": "cat_refund"}  # expense slot dropped


def test_add_alias_dedupes_and_bump_usage_takes_max_date(db_engine):
    store = PayeeStore(engine=db_engine)
    payee = store.create("Netflix", aliases=["netflix"])
    store.add_alias(payee["id"], "netflix.com")
    store.add_alias(payee["id"], "netflix.com")  # dup — no-op
    assert store.find_by_id(payee["id"])["aliases"] == ["netflix", "netflix.com"]

    store.bump_usage(payee["id"], when="2026-01-05T00:00:00Z")
    store.bump_usage(payee["id"], when="2026-01-01T00:00:00Z")  # earlier — must not regress
    assert store.find_by_id(payee["id"])["last_used"] == "2026-01-05"


def test_bump_usage_missing_payee_returns_none_never_raises(db_engine):
    store = PayeeStore(engine=db_engine)
    assert store.bump_usage("pay_missing", when="2026-01-01") is None
