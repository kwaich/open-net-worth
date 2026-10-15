"""``plan_state`` life-event ``amount`` sign flip — a data migration, not a schema change.

The Outlook engine and every UI surface switched from "+amount = spend, -amount = windfall"
to the standard "-amount = spend, +amount = windfall" (see planData.ts / outlook.tsx /
charts.tsx). Without a backfill, every already-saved baseline/scenario event silently flips
MEANING the moment the new frontend ships — an existing $500k home purchase would start being
simulated as a $500k windfall. This migration negates every stored event amount so the
MEANING is preserved under the new convention.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import sqlalchemy as sa


def _load_migration():
    """The revision's module name starts with a digit, so it cannot be imported by name."""
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "backend/db/alembic/versions/202609032128_flip_plan_event_amount_sign.py"
    )
    spec = importlib.util.spec_from_file_location("_flip_event_sign_rev", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


flip = _load_migration()


def _db():
    engine = sa.create_engine("sqlite://")
    conn = engine.connect()
    conn.execute(sa.text("CREATE TABLE plan_state (book_id TEXT PRIMARY KEY, payload TEXT)"))
    return conn


def _insert(conn, book_id: str, payload: dict) -> None:
    conn.execute(
        sa.text("INSERT INTO plan_state VALUES (:b, :p)"),
        {"b": book_id, "p": json.dumps(payload)},
    )


def _payload(conn, book_id: str = "book_1") -> dict:
    row = conn.execute(
        sa.text("SELECT payload FROM plan_state WHERE book_id = :b"), {"b": book_id}
    ).scalar_one()
    return json.loads(row)


def test_negates_baseline_and_scenario_event_amounts(monkeypatch):
    conn = _db()
    _insert(
        conn,
        "book_1",
        {
            "baseline": {
                "retireAge": 45,
                "events": [{"id": "e1", "label": "Home", "amount": 500000, "age": 30}],
            },
            "scenarios": [
                {
                    "id": "scen_1",
                    "events": [{"id": "e2", "label": "Porsche", "amount": -50000, "age": 40}],
                }
            ],
        },
    )
    monkeypatch.setattr(flip.op, "get_bind", lambda: conn)

    flip.upgrade()

    data = _payload(conn)
    assert data["baseline"]["events"] == [
        {"id": "e1", "label": "Home", "amount": -500000, "age": 30}
    ]
    assert data["scenarios"][0]["events"] == [
        {"id": "e2", "label": "Porsche", "amount": 50000, "age": 40}
    ]


def test_leaves_streams_and_non_event_fields_untouched(monkeypatch):
    conn = _db()
    _insert(
        conn,
        "book_1",
        {
            "baseline": {
                "retireAge": 45,
                "streams": [{"label": "CPF LIFE", "amount": 2000, "age": 65}],
                "events": [{"id": "e1", "amount": 100000, "age": 30}],
            },
            "scenarios": [],
        },
    )
    monkeypatch.setattr(flip.op, "get_bind", lambda: conn)

    flip.upgrade()

    data = _payload(conn)
    assert data["baseline"]["streams"] == [{"label": "CPF LIFE", "amount": 2000, "age": 65}]
    assert data["baseline"]["retireAge"] == 45


def test_no_ops_on_a_null_baseline_and_empty_scenarios(monkeypatch):
    conn = _db()
    _insert(conn, "book_1", {"baseline": None, "scenarios": []})
    monkeypatch.setattr(flip.op, "get_bind", lambda: conn)

    flip.upgrade()

    assert _payload(conn) == {"baseline": None, "scenarios": []}


def test_no_ops_on_a_baseline_with_no_events(monkeypatch):
    conn = _db()
    _insert(conn, "book_1", {"baseline": {"retireAge": 50}, "scenarios": []})
    monkeypatch.setattr(flip.op, "get_bind", lambda: conn)

    flip.upgrade()

    assert _payload(conn) == {"baseline": {"retireAge": 50}, "scenarios": []}


def test_downgrade_re_negates_back_to_the_original_sign(monkeypatch):
    conn = _db()
    _insert(
        conn,
        "book_1",
        {"baseline": {"events": [{"id": "e1", "amount": 500000, "age": 30}]}, "scenarios": []},
    )
    monkeypatch.setattr(flip.op, "get_bind", lambda: conn)

    flip.upgrade()
    flip.downgrade()

    assert _payload(conn)["baseline"]["events"] == [{"id": "e1", "amount": 500000, "age": 30}]


def test_covers_every_book_row(monkeypatch):
    conn = _db()
    _insert(conn, "book_1", {"baseline": {"events": [{"id": "e1", "amount": 1, "age": 30}]}})
    _insert(conn, "book_2", {"baseline": {"events": [{"id": "e2", "amount": 2, "age": 30}]}})
    monkeypatch.setattr(flip.op, "get_bind", lambda: conn)

    flip.upgrade()

    assert _payload(conn, "book_1")["baseline"]["events"][0]["amount"] == -1
    assert _payload(conn, "book_2")["baseline"]["events"][0]["amount"] == -2
