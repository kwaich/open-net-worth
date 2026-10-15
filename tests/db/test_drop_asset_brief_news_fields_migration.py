"""Revision 202608301645 — drops ``news``/``news_checked_at`` from every ``asset_brief``
payload (RFC ``intel-signals-data-model`` §5: the Asset Detail news feed becomes a live query
over `evidence`, so this cache no longer stores its own copy).

Runs the migration for REAL (``command.upgrade`` from the schema-split revision it chains
onto) against hand-seeded PRE-migration payloads, mirroring
``tests/db/test_automation_always_allowed_migration.py``'s shape — ``asset_brief`` has no
``book_id`` column (unlike ``automations``), so the seed/read helpers below drop that column.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_FROM_REVISION = "202608301500_intel_stories_split"
_TARGET_REVISION = "202608301645_drop_asset_brief_news_fields"


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
            text("INSERT INTO asset_brief (seq, id, payload) VALUES (:seq, :id, :payload)"),
            {"seq": seq, "id": row_id, "payload": json.dumps(payload)},
        )


def _payload(engine, row_id: str) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT payload FROM asset_brief WHERE id = :id"), {"id": row_id}
        ).fetchone()
    raw = row[0]
    return json.loads(raw) if isinstance(raw, str) else raw


def _upgrade(tmp_path, target: str = _TARGET_REVISION) -> None:
    command.upgrade(make_config(f"sqlite:///{tmp_path / 'onw.db'}"), target)


def test_upgrade_drops_news_and_news_checked_at_keeps_everything_else(_engine, tmp_path):
    _seed(
        _engine,
        "inst-1",
        {
            "instrument_id": "inst-1",
            "news": [{"title": "Old cached headline", "url": "https://example.com/a"}],
            "news_checked_at": "2026-08-29T00:00:00+00:00",
            "key_issues": [{"question": "Q"}],
            "description": "A company.",
        },
    )

    _upgrade(tmp_path)

    payload = _payload(_engine, "inst-1")
    assert "news" not in payload
    assert "news_checked_at" not in payload
    assert payload["key_issues"] == [{"question": "Q"}]
    assert payload["description"] == "A company."
    assert payload["instrument_id"] == "inst-1"


def test_upgrade_is_a_no_op_when_the_keys_are_absent(_engine, tmp_path):
    _seed(_engine, "inst-1", {"instrument_id": "inst-1", "description": "No news ever cached."})

    _upgrade(tmp_path)

    assert _payload(_engine, "inst-1") == {
        "instrument_id": "inst-1",
        "description": "No news ever cached.",
    }


def test_one_unreadable_payload_does_not_stop_the_others(_engine, tmp_path):
    with _engine.begin() as conn:
        conn.execute(
            text("INSERT INTO asset_brief (seq, id, payload) VALUES (1, 'broken', 'not json')")
        )
    _seed(_engine, "inst-2", {"instrument_id": "inst-2", "news": []}, seq=2)

    _upgrade(tmp_path)

    assert "news" not in _payload(_engine, "inst-2")


def test_downgrade_restores_empty_placeholder_keys(_engine, tmp_path):
    _seed(
        _engine,
        "inst-1",
        {
            "instrument_id": "inst-1",
            "news": [{"title": "Real cached article"}],
            "news_checked_at": "2026-08-29T00:00:00+00:00",
        },
    )

    _upgrade(tmp_path)
    command.downgrade(make_config(f"sqlite:///{tmp_path / 'onw.db'}"), _FROM_REVISION)

    payload = _payload(_engine, "inst-1")
    assert payload["news"] == []
    assert payload["news_checked_at"] is None
    assert payload["instrument_id"] == "inst-1"
