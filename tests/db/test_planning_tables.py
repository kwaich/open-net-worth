from sqlalchemy import Text, inspect

from backend.db import tables as t

BOOK_PAYLOAD_TABLES = [
    "envelope_groups",
    "categories",
    "commitments",
    "envelopes",
    "income_events",
    "recurring_streams",
    "targets",
    "corrections",
    "match_decisions",
    "match_reviews",
]


def test_all_twelve_planning_tables_registered():
    for name in BOOK_PAYLOAD_TABLES + ["budget_assignments", "plan_state"]:
        assert name in t.metadata.tables


def test_book_payload_tables_are_book_scoped_single_pk():
    for name in BOOK_PAYLOAD_TABLES:
        table = t.metadata.tables[name]
        assert [c.name for c in table.primary_key.columns] == ["id"]  # single-col PK (write_log)
        assert {c.name for c in table.c} == {"book_id", "seq", "id", "payload"}
        assert "book_id" in [fk.parent.name for fk in table.foreign_keys]  # FK -> books.id


def test_budget_assignments_has_book_id_and_text_amount():
    assert isinstance(t.budget_assignments.c.amount.type, Text)  # decimal-as-text, never Float
    assert "book_id" in t.budget_assignments.c
    assert [c.name for c in t.budget_assignments.primary_key.columns] == ["id"]


def test_plan_state_is_keyed_by_book_id():
    assert {c.name for c in t.plan_state.c} == {"book_id", "payload"}
    assert [c.name for c in t.plan_state.primary_key.columns] == ["book_id"]


def test_planning_schema_creates_on_both_dialects(db_engine):
    insp = inspect(db_engine)  # SQLite here, Postgres in CI
    for name in BOOK_PAYLOAD_TABLES + ["budget_assignments", "plan_state"]:
        assert insp.has_table(name)
