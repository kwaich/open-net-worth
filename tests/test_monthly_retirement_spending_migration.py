from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest
import sqlalchemy as sa


def _migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "backend/db/alembic/versions/202609111200_monthly_retirement_spending.py"
    )
    spec = importlib.util.spec_from_file_location("_monthly_retirement_spending", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(conn, table: str) -> dict:
    raw = conn.execute(sa.text(f"SELECT payload FROM {table}")).scalar_one()
    return json.loads(raw)


def test_key_conversion_accepts_only_an_equivalent_existing_destination():
    migration = _migration()
    payload = {
        "fire_annual_expenses": 42_000,
        "fire_monthly_retirement_spending": 3_500,
    }

    assert migration._convert_key(
        payload,
        "fire_annual_expenses",
        "fire_monthly_retirement_spending",
        1 / 12,
    )
    assert payload == {"fire_monthly_retirement_spending": 3_500}


def test_key_conversion_refuses_a_conflicting_existing_destination():
    migration = _migration()
    payload = {
        "fire_annual_expenses": 42_000,
        "fire_monthly_retirement_spending": 4_000,
    }

    with pytest.raises(RuntimeError, match="conflicting"):
        migration._convert_key(
            payload,
            "fire_annual_expenses",
            "fire_monthly_retirement_spending",
            1 / 12,
        )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_key_conversion_refuses_non_finite_values(value):
    migration = _migration()

    with pytest.raises(RuntimeError, match="finite"):
        migration._convert_key(
            {"fire_annual_expenses": value},
            "fire_annual_expenses",
            "fire_monthly_retirement_spending",
            1 / 12,
        )


def test_upgrade_converts_profile_and_all_plan_spending_to_monthly(monkeypatch):
    migration = _migration()
    conn = sa.create_engine("sqlite://").connect()
    conn.execute(sa.text("CREATE TABLE profile (book_id TEXT PRIMARY KEY, payload TEXT)"))
    conn.execute(sa.text("CREATE TABLE plan_state (book_id TEXT PRIMARY KEY, payload TEXT)"))
    conn.execute(
        sa.text("INSERT INTO profile VALUES ('default', :payload)"),
        {"payload": json.dumps({"fire_annual_expenses": 42_000, "name": "QA"})},
    )
    conn.execute(
        sa.text("INSERT INTO plan_state VALUES ('default', :payload)"),
        {
            "payload": json.dumps(
                {
                    "baseline": {"spend": 36_000, "ret": 7},
                    "scenarios": [{"id": "one", "spend": 60_000}],
                }
            )
        },
    )
    monkeypatch.setattr(migration.op, "get_bind", lambda: conn)

    migration.upgrade()

    assert _payload(conn, "profile") == {
        "fire_monthly_retirement_spending": 3_500,
        "name": "QA",
    }
    assert _payload(conn, "plan_state") == {
        "baseline": {"monthlyRetirementSpending": 3_000, "ret": 7},
        "scenarios": [{"id": "one", "monthlyRetirementSpending": 5_000}],
    }


def test_downgrade_restores_annual_profile_and_plan_values(monkeypatch):
    migration = _migration()
    conn = sa.create_engine("sqlite://").connect()
    conn.execute(sa.text("CREATE TABLE profile (book_id TEXT PRIMARY KEY, payload TEXT)"))
    conn.execute(sa.text("CREATE TABLE plan_state (book_id TEXT PRIMARY KEY, payload TEXT)"))
    conn.execute(
        sa.text("INSERT INTO profile VALUES ('default', :payload)"),
        {"payload": json.dumps({"fire_monthly_retirement_spending": 3_500})},
    )
    conn.execute(
        sa.text("INSERT INTO plan_state VALUES ('default', :payload)"),
        {
            "payload": json.dumps(
                {
                    "baseline": {"monthlyRetirementSpending": 3_000},
                    "scenarios": [{"monthlyRetirementSpending": 5_000}],
                }
            )
        },
    )
    monkeypatch.setattr(migration.op, "get_bind", lambda: conn)

    migration.downgrade()

    assert _payload(conn, "profile") == {"fire_annual_expenses": 42_000}
    assert _payload(conn, "plan_state") == {
        "baseline": {"spend": 36_000},
        "scenarios": [{"spend": 60_000}],
    }
