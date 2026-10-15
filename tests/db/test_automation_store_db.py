"""AutomationStore on BookPayloadTableStore (plan 4d, task 2) — DB-CRUD round trip plus the
tombstone-visibility parity (`all_persisted`/`find_persisted_by_id` see a `status="deleting"`
row that `all`/`find_by_id` must hide).
"""

from __future__ import annotations

from backend.db.automation_store import AutomationStore


def _automation(**overrides) -> dict:
    return {
        "name": "Categorize coffee",
        "instructions": "Set category to Food when payee is Starbucks",
        **overrides,
    }


def test_create_round_trip(db_engine):
    store = AutomationStore(engine=db_engine)
    row = store.create(_automation())
    assert row["id"].startswith("automation_")
    assert row["status"] == "active"
    assert store.find_by_id(row["id"])["name"] == "Categorize coffee"


def test_create_rejects_invalid_status(db_engine):
    store = AutomationStore(engine=db_engine)
    try:
        store.create(_automation(status="deleting"))
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_active_and_due_filters(db_engine):
    store = AutomationStore(engine=db_engine)
    scheduled = store.create(_automation(name="A", next_run_at="2026-01-01T00:00:00+00:00"))
    paused = store.create(_automation(name="B", status="paused"))

    # active() returns every LIVE_STATUSES row (active AND paused) — a status filter, not a
    # "runnable now" filter.
    assert {r["id"] for r in store.active()} == {scheduled["id"], paused["id"]}

    due = store.due("2026-06-01T00:00:00+00:00")
    assert [r["id"] for r in due] == [scheduled["id"]]
    assert store.due("2025-01-01T00:00:00+00:00") == []  # before next_run_at — not due yet


# ── Domain parity: tombstone visibility (the brief's own named test) ───────────────────────


def test_automation_tombstone_visible_to_persisted_reads(db_engine):
    """all_persisted() sees a status="deleting" row; all()/find_by_id() do not."""
    store = AutomationStore(engine=db_engine)
    row = store.create(_automation())

    # Simulate the tombstone transition a soft-delete leaves behind (upsert bypasses the
    # create-time status validation, matching how a real deletion flow marks a row).
    store.upsert({**row, "status": "deleting"})

    assert store.find_by_id(row["id"]) is None
    assert row["id"] not in {r["id"] for r in store.all()}

    persisted = store.find_persisted_by_id(row["id"])
    assert persisted is not None and persisted["status"] == "deleting"
    assert row["id"] in {r["id"] for r in store.all_persisted()}


def test_book_id_stamped_on_insert(db_engine):
    from sqlalchemy import select

    from backend.db.tables import DEFAULT_BOOK_ID, automations

    store = AutomationStore(engine=db_engine)
    row = store.create(_automation())
    with db_engine.connect() as conn:
        db_row = conn.execute(select(automations).where(automations.c.id == row["id"])).first()
    assert db_row.book_id == DEFAULT_BOOK_ID
