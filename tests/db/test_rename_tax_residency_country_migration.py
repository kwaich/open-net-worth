"""Revision 0059 (``backend.db.alembic.versions.202608260008_rename_tax_residency_country``) —
renames the ``tax_residency_country`` profile-payload key to ``country_of_residence``.

Runs the migration for REAL (``command.upgrade`` from ``"202608260007_drop_profile_tax_config"`` to ``"202608260008_rename_tax_residency_country"``, on an
isolated tmp-path DB) against a hand-seeded PRE-migration payload, mirroring
``tests/db/test_derived_data_colocation_migration.py``'s own shape.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config


@pytest.fixture
def _engine(tmp_path):
    db_path = tmp_path / "onw.db"
    engine = create_engine(f"sqlite:///{db_path}")
    command.upgrade(make_config(f"sqlite:///{db_path}"), "202608260007_drop_profile_tax_config")
    yield engine
    engine.dispose()


def _seed(engine, book_id: str, payload: dict) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO profile (book_id, payload) VALUES (:book_id, :payload)"),
            {"book_id": book_id, "payload": json.dumps(payload)},
        )


def _payload(engine, book_id: str) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT payload FROM profile WHERE book_id = :book_id"), {"book_id": book_id}
        ).fetchone()
    raw = row[0]
    return json.loads(raw) if isinstance(raw, str) else raw


def test_upgrade_renames_the_key(_engine, tmp_path):
    _seed(_engine, "default", {"name": "Alex Tan", "tax_residency_country": "MY"})

    db_path = tmp_path / "onw.db"
    command.upgrade(
        make_config(f"sqlite:///{db_path}"), "202608260008_rename_tax_residency_country"
    )

    payload = _payload(_engine, "default")
    assert "tax_residency_country" not in payload
    assert payload["country_of_residence"] == "MY"
    assert payload["name"] == "Alex Tan"


def test_upgrade_is_a_no_op_when_the_key_is_absent(_engine, tmp_path):
    _seed(_engine, "default", {"name": "Alex Tan"})

    db_path = tmp_path / "onw.db"
    command.upgrade(
        make_config(f"sqlite:///{db_path}"), "202608260008_rename_tax_residency_country"
    )

    payload = _payload(_engine, "default")
    assert payload == {"name": "Alex Tan"}


def test_upgrade_skips_an_unparseable_payload_rather_than_failing(_engine, tmp_path):
    with _engine.begin() as conn:
        conn.execute(
            text("INSERT INTO profile (book_id, payload) VALUES (:book_id, :payload)"),
            {"book_id": "broken", "payload": "{not valid json"},
        )
    _seed(_engine, "default", {"tax_residency_country": "DE"})

    db_path = tmp_path / "onw.db"
    command.upgrade(
        make_config(f"sqlite:///{db_path}"), "202608260008_rename_tax_residency_country"
    )

    assert _payload(_engine, "default")["country_of_residence"] == "DE"
    with _engine.begin() as conn:
        row = conn.execute(text("SELECT payload FROM profile WHERE book_id = 'broken'")).fetchone()
    assert row[0] == "{not valid json"


def test_downgrade_reverses_the_rename(_engine, tmp_path):
    _seed(_engine, "default", {"country_of_residence": "AU"})

    db_path = tmp_path / "onw.db"
    command.upgrade(
        make_config(f"sqlite:///{db_path}"), "202608260008_rename_tax_residency_country"
    )
    command.downgrade(make_config(f"sqlite:///{db_path}"), "202608260007_drop_profile_tax_config")

    payload = _payload(_engine, "default")
    assert "country_of_residence" not in payload
    assert payload["tax_residency_country"] == "AU"
