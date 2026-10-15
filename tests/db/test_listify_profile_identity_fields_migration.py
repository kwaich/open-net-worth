"""Revision 0060 (``backend.db.alembic.versions.202608260009_listify_profile_identity_fields``) —
converts the profile's scalar identity fields to lists and renames them to plural:
``nationality`` -> ``nationalities``, ``country_of_residence`` -> ``countries_of_residence``,
``employment_type`` -> ``employment_types``.

Runs the migration for REAL (``command.upgrade`` from ``"202608260008_rename_tax_residency_country"`` to ``"202608260009_listify_profile_identity_fields"``, on an
isolated tmp-path DB) against a hand-seeded PRE-migration payload, mirroring
``tests/db/test_rename_tax_residency_country_migration.py``'s own shape.
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
    command.upgrade(
        make_config(f"sqlite:///{db_path}"), "202608260008_rename_tax_residency_country"
    )
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


def test_upgrade_listifies_and_renames_nonempty_scalars(_engine, tmp_path):
    _seed(
        _engine,
        "default",
        {
            "name": "Tai",
            "nationality": "VN",
            "country_of_residence": "SG",
            "employment_type": "salaried",
        },
    )

    db_path = tmp_path / "onw.db"
    command.upgrade(
        make_config(f"sqlite:///{db_path}"), "202608260009_listify_profile_identity_fields"
    )

    payload = _payload(_engine, "default")
    assert "nationality" not in payload
    assert "country_of_residence" not in payload
    assert "employment_type" not in payload
    assert payload["nationalities"] == ["VN"]
    assert payload["countries_of_residence"] == ["SG"]
    assert payload["employment_types"] == ["salaried"]
    assert payload["name"] == "Tai"


def test_upgrade_turns_empty_or_missing_scalars_into_empty_lists(_engine, tmp_path):
    _seed(_engine, "default", {"nationality": "", "country_of_residence": None})

    db_path = tmp_path / "onw.db"
    command.upgrade(
        make_config(f"sqlite:///{db_path}"), "202608260009_listify_profile_identity_fields"
    )

    payload = _payload(_engine, "default")
    assert payload["nationalities"] == []
    assert payload["countries_of_residence"] == []
    assert "employment_types" not in payload


def test_upgrade_is_a_no_op_when_no_keys_are_present(_engine, tmp_path):
    _seed(_engine, "default", {"name": "Tai"})

    db_path = tmp_path / "onw.db"
    command.upgrade(
        make_config(f"sqlite:///{db_path}"), "202608260009_listify_profile_identity_fields"
    )

    payload = _payload(_engine, "default")
    assert payload == {"name": "Tai"}


def test_upgrade_skips_an_unparseable_payload_rather_than_failing(_engine, tmp_path):
    with _engine.begin() as conn:
        conn.execute(
            text("INSERT INTO profile (book_id, payload) VALUES (:book_id, :payload)"),
            {"book_id": "broken", "payload": "{not valid json"},
        )
    _seed(_engine, "default", {"nationality": "US"})

    db_path = tmp_path / "onw.db"
    command.upgrade(
        make_config(f"sqlite:///{db_path}"), "202608260009_listify_profile_identity_fields"
    )

    assert _payload(_engine, "default")["nationalities"] == ["US"]
    with _engine.begin() as conn:
        row = conn.execute(text("SELECT payload FROM profile WHERE book_id = 'broken'")).fetchone()
    assert row[0] == "{not valid json"


def test_downgrade_reverses_the_rename_keeping_the_first_entry(_engine, tmp_path):
    _seed(
        _engine,
        "default",
        {
            "nationalities": ["VN", "US"],
            "countries_of_residence": [],
            "employment_types": ["salaried"],
        },
    )

    db_path = tmp_path / "onw.db"
    command.upgrade(
        make_config(f"sqlite:///{db_path}"), "202608260009_listify_profile_identity_fields"
    )
    command.downgrade(
        make_config(f"sqlite:///{db_path}"), "202608260008_rename_tax_residency_country"
    )

    payload = _payload(_engine, "default")
    assert "nationalities" not in payload
    assert "countries_of_residence" not in payload
    assert "employment_types" not in payload
    assert payload["nationality"] == "VN"
    assert payload["country_of_residence"] is None
    assert payload["employment_type"] == "salaried"
