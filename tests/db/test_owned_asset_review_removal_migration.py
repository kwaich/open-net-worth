"""Real migration coverage for removing asset review scheduling and typing legacy facts."""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_FROM = "202609091200_statement_parse_progress"


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "onw.db"
    url = f"sqlite:///{path}"
    command.upgrade(make_config(url), _FROM)
    engine = create_engine(url)
    yield engine, url
    engine.dispose()


def _payload(conn, table: str, row_id: str) -> dict:
    raw = conn.execute(text(f"SELECT payload FROM {table} WHERE id = :id"), {"id": row_id}).scalar()
    return json.loads(raw) if isinstance(raw, str) else raw


def test_upgrade_rewrites_asset_details_and_removes_only_asset_review_runtime(legacy_db):
    engine, url = legacy_db
    asset = {
        "id": "oa_legacy",
        "name": "Legacy watch",
        "cadence_months": 12,
        "automation_id": "auto_asset",
        "facts": [["Insured", "NTUC comprehensive"], ["Matures", "Jun 2031"], ["Serial", "A1"]],
        "marks": [{"date": "2025-07-01", "value": 100}],
    }
    category = {"key": "c_test", "label": "Test", "cadence_months": 12}
    asset_auto = {"id": "auto_asset", "provenance": {"system_key": "asset_remark:oa_legacy"}}
    unrelated = {"id": "auto_keep", "provenance": {"system_key": "other"}}
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO owned_assets (book_id, seq, id, payload) VALUES ('default', 1, 'oa_legacy', :payload)"
            ),
            {"payload": json.dumps(asset)},
        )
        conn.execute(
            text(
                "INSERT INTO owned_asset_categories (book_id, seq, id, payload) VALUES ('default', 1, 'c_test', :payload)"
            ),
            {"payload": json.dumps(category)},
        )
        for seq, row in enumerate((asset_auto, unrelated), 1):
            conn.execute(
                text(
                    "INSERT INTO automations (book_id, seq, id, payload) VALUES ('default', :seq, :id, :payload)"
                ),
                {"seq": seq, "id": row["id"], "payload": json.dumps(row)},
            )
        conn.execute(
            text("INSERT INTO automation_runtime (seq, id, payload) VALUES (1, 'auto_asset', '{}')")
        )

    command.upgrade(make_config(url), "head")

    with engine.begin() as conn:
        migrated = _payload(conn, "owned_assets", "oa_legacy")
        assert migrated["insured"] is True
        assert migrated["maturity_date"] == "2031-06-01"
        assert ["Insurance details", "NTUC comprehensive"] in migrated["facts"]
        assert ["Serial", "A1"] in migrated["facts"]
        assert {"cadence_months", "automation_id"}.isdisjoint(migrated)
        assert "cadence_months" not in _payload(conn, "owned_asset_categories", "c_test")
        automation_ids = set(conn.execute(text("SELECT id FROM automations")).scalars())
        runtime_ids = set(conn.execute(text("SELECT id FROM automation_runtime")).scalars())
        assert automation_ids == {"auto_keep"}
        assert "auto_asset" not in runtime_ids


@pytest.mark.parametrize("payload", ["not-json", json.dumps(["not", "an", "object"])])
def test_upgrade_refuses_malformed_live_asset_payload(legacy_db, payload):
    engine, url = legacy_db
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO owned_assets (book_id, seq, id, payload) "
                "VALUES ('default', 1, 'oa_bad', :payload)"
            ),
            {"payload": payload},
        )

    with pytest.raises(RuntimeError, match="migration refused"):
        command.upgrade(make_config(url), "head")

    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == _FROM
