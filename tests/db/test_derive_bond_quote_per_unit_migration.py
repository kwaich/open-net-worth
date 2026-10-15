"""Revision 202609051200 — derives ``quote_per_unit`` from ``bond_terms.face_value`` on
every stored bond instrument (MATH-50: nothing ever set it, so bonds valued 10x-100x off).

Runs the migration for REAL (``command.upgrade`` from the revision it chains onto) against
hand-seeded PRE-migration payloads, mirroring
``tests/db/test_drop_asset_brief_news_fields_migration.py``'s shape — ``instruments`` is a
plain ``payload_table`` (seq/id/payload, no ``book_id``), same as ``asset_brief``.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_FROM_REVISION = "202609032128_flip_plan_event_amount_sign"
_TARGET_REVISION = "202609051200_derive_bond_quote_per_unit"


@pytest.fixture
def _engine(tmp_path):
    db_path = tmp_path / "onw.db"
    engine = create_engine(f"sqlite:///{db_path}")
    command.upgrade(make_config(f"sqlite:///{db_path}"), _FROM_REVISION)
    yield engine
    engine.dispose()


def _seed(engine, row_id: str, payload: dict, *, seq: int = 1) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO instruments (seq, id, payload) VALUES (:seq, :id, :payload)"),
            {"seq": seq, "id": row_id, "payload": json.dumps(payload)},
        )


def _payload(engine, row_id: str) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT payload FROM instruments WHERE id = :id"), {"id": row_id}
        ).fetchone()
    raw = row[0]
    return json.loads(raw) if isinstance(raw, str) else raw


def _upgrade(tmp_path, target: str = _TARGET_REVISION) -> None:
    command.upgrade(make_config(f"sqlite:///{tmp_path / 'onw.db'}"), target)


def test_upgrade_derives_quote_per_unit_for_market_bond(_engine, tmp_path):
    _seed(
        _engine,
        "inst-corp",
        {
            "id": "inst-corp",
            "symbol": "CORP",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "quote_per_unit": 1.0,
            "bond_terms": {"subtype": "market", "face_value": 1000.0},
        },
    )

    _upgrade(tmp_path)

    payload = _payload(_engine, "inst-corp")
    assert payload["quote_per_unit"] == 10.0
    assert payload["bond_terms"]["face_value"] == 1000.0  # untouched


def test_upgrade_derives_quote_per_unit_for_ssb_face_one(_engine, tmp_path):
    _seed(
        _engine,
        "inst-ssb",
        {
            "id": "inst-ssb",
            "symbol": "SSB",
            "asset_class": "bond",
            "quote_currency": "SGD",
            "quote_per_unit": 1.0,
            "bond_terms": {"subtype": "ssb", "face_value": 1.0},
        },
        seq=2,
    )

    _upgrade(tmp_path)

    assert _payload(_engine, "inst-ssb")["quote_per_unit"] == 0.01


def test_upgrade_is_idempotent(_engine, tmp_path):
    _seed(
        _engine,
        "inst-corp",
        {
            "id": "inst-corp",
            "asset_class": "bond",
            "quote_per_unit": 1.0,
            "bond_terms": {"subtype": "market", "face_value": 1000.0},
        },
    )

    _upgrade(tmp_path)
    _upgrade(tmp_path)  # re-running must not error or change the result

    assert _payload(_engine, "inst-corp")["quote_per_unit"] == 10.0


def test_upgrade_leaves_non_bond_instruments_untouched(_engine, tmp_path):
    _seed(
        _engine,
        "inst-eq",
        {
            "id": "inst-eq",
            "symbol": "AAPL",
            "asset_class": "equity",
            "quote_currency": "USD",
            "quote_per_unit": 1.0,
        },
    )

    _upgrade(tmp_path)

    assert _payload(_engine, "inst-eq")["quote_per_unit"] == 1.0


def test_upgrade_leaves_bond_with_no_bond_terms_untouched(_engine, tmp_path):
    _seed(
        _engine,
        "inst-nobond",
        {
            "id": "inst-nobond",
            "symbol": "X",
            "asset_class": "bond",
            "quote_currency": "USD",
            "quote_per_unit": 1.0,
            "bond_terms": None,
        },
    )

    _upgrade(tmp_path)

    assert _payload(_engine, "inst-nobond")["quote_per_unit"] == 1.0


def test_one_unreadable_payload_does_not_stop_the_others(_engine, tmp_path):
    with _engine.begin() as conn:
        conn.execute(
            text("INSERT INTO instruments (seq, id, payload) VALUES (1, 'broken', 'not json')")
        )
    _seed(
        _engine,
        "inst-corp",
        {
            "id": "inst-corp",
            "asset_class": "bond",
            "quote_per_unit": 1.0,
            "bond_terms": {"subtype": "market", "face_value": 1000.0},
        },
        seq=2,
    )

    _upgrade(tmp_path)

    assert _payload(_engine, "inst-corp")["quote_per_unit"] == 10.0


def test_downgrade_is_a_no_op(_engine, tmp_path):
    _seed(
        _engine,
        "inst-corp",
        {
            "id": "inst-corp",
            "asset_class": "bond",
            "quote_per_unit": 1.0,
            "bond_terms": {"subtype": "market", "face_value": 1000.0},
        },
    )

    _upgrade(tmp_path)
    command.downgrade(make_config(f"sqlite:///{tmp_path / 'onw.db'}"), _FROM_REVISION)

    # No-op downgrade: the derived value is left in place (nothing correct to revert to).
    assert _payload(_engine, "inst-corp")["quote_per_unit"] == 10.0
