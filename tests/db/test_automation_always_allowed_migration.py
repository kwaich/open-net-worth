"""Revision 0061 — renames the Automation payload key ``allowed`` to ``always_allowed``.

Runs the migration for REAL (``command.upgrade`` from ``"202608260009_listify_profile_identity_fields"`` to ``"202608271618_rename_automation_allowed_to_always_allowed"`` on an
isolated tmp-path DB) against hand-seeded PRE-migration payloads, mirroring
``tests/db/test_rename_tax_residency_country_migration.py``'s shape.

The rename matters because the old name was WRONG in the dangerous direction. The list has
exactly one consumer — ``AutomationApprovalContext.always_allowed_tools``, which makes the
gate return ``"allow"`` — and it never governed reachability: an Automation with
``allowed: []`` can call every tool in the app, it is simply asked first. So ``allowed: []``
read as a lockdown and was not one.
"""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_PERM = {"src": "Open Net Worth", "desc": "Send reminders.", "tools": ["telegram_send_message"]}


@pytest.fixture
def _engine(tmp_path):
    db_path = tmp_path / "onw.db"
    engine = create_engine(f"sqlite:///{db_path}")
    command.upgrade(
        make_config(f"sqlite:///{db_path}"), "202608260009_listify_profile_identity_fields"
    )
    yield engine
    engine.dispose()


def _seed(engine, row_id: str, payload: dict, *, seq: int = 1) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO automations (book_id, seq, id, payload) "
                "VALUES ('default', :seq, :id, :payload)"
            ),
            {"seq": seq, "id": row_id, "payload": json.dumps(payload)},
        )


def _payload(engine, row_id: str) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT payload FROM automations WHERE id = :id"), {"id": row_id}
        ).fetchone()
    raw = row[0]
    return json.loads(raw) if isinstance(raw, str) else raw


def _upgrade(
    tmp_path, target: str = "202608271618_rename_automation_allowed_to_always_allowed"
) -> None:
    command.upgrade(make_config(f"sqlite:///{tmp_path / 'onw.db'}"), target)


def test_upgrade_renames_the_key_and_keeps_the_entries(_engine, tmp_path):
    _seed(_engine, "auto_1", {"name": "Reminders", "approval": "auto", "allowed": [_PERM]})

    _upgrade(tmp_path)

    payload = _payload(_engine, "auto_1")
    assert "allowed" not in payload
    assert payload["always_allowed"] == [_PERM]
    assert payload["name"] == "Reminders" and payload["approval"] == "auto"


def test_an_empty_list_is_renamed_too(_engine, tmp_path):
    """The empty case is the COMMON one — 25 of this instance's 27 live rows. Leaving it
    behind would strand a key nothing reads, and ``AutomationStore._canonical`` writes
    ``always_allowed`` on the row's next save regardless, so the row would carry both."""
    _seed(_engine, "auto_1", {"name": "Reminders", "allowed": []})

    _upgrade(tmp_path)

    payload = _payload(_engine, "auto_1")
    assert "allowed" not in payload
    assert payload["always_allowed"] == []


def test_upgrade_is_a_no_op_when_the_key_is_absent(_engine, tmp_path):
    _seed(_engine, "auto_1", {"name": "Reminders"})

    _upgrade(tmp_path)

    assert _payload(_engine, "auto_1") == {"name": "Reminders"}


def test_one_unreadable_payload_does_not_stop_the_others(_engine, tmp_path):
    """0059/0060's rule: a row that cannot be parsed is skipped, never a failed boot."""
    with _engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO automations (book_id, seq, id, payload) "
                "VALUES ('default', 1, 'broken', 'not json')"
            )
        )
    _seed(_engine, "auto_2", {"name": "Fine", "allowed": [_PERM]}, seq=2)

    _upgrade(tmp_path)

    assert _payload(_engine, "auto_2")["always_allowed"] == [_PERM]


def test_downgrade_restores_the_old_key_exactly(_engine, tmp_path):
    original = {"name": "Reminders", "approval": "auto", "allowed": [_PERM]}
    _seed(_engine, "auto_1", original)

    _upgrade(tmp_path)
    command.downgrade(
        make_config(f"sqlite:///{tmp_path / 'onw.db'}"),
        "202608260009_listify_profile_identity_fields",
    )

    assert _payload(_engine, "auto_1") == original
