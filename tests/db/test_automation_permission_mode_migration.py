"""Revision 0062 — renames the Automation payload key ``approval`` to ``permission_mode``.

Runs the migration for REAL (``command.upgrade`` from ``"202608271618_rename_automation_allowed_to_always_allowed"`` to ``"202608271619_rename_automation_approval_to_permission_mode"`` on an
isolated tmp-path DB) against hand-seeded PRE-migration payloads, mirroring
``tests/db/test_automation_always_allowed_migration.py``'s shape.

``approval`` named the thing being decided rather than the setting — it reads as a per-run
outcome ("was this approved?") when it is a standing three-way policy: ``manual`` asks on
every approval-tier call, ``auto`` proceeds except on hardline operations, ``skip`` proceeds
regardless. It is the blanket half of the same decision ``always_allowed`` (revision 0061)
carves per-tool exceptions out of.
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
        make_config(f"sqlite:///{db_path}"),
        "202608271618_rename_automation_allowed_to_always_allowed",
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
    tmp_path, target: str = "202608271619_rename_automation_approval_to_permission_mode"
) -> None:
    command.upgrade(make_config(f"sqlite:///{tmp_path / 'onw.db'}"), target)


@pytest.mark.parametrize("mode", ["manual", "auto", "skip"])
def test_upgrade_renames_the_key_for_every_mode(_engine, tmp_path, mode):
    """All three values carry across — including ``skip``, the one whose loss would silently
    make a run start asking again (or, on a botched reverse, stop asking)."""
    _seed(_engine, "auto_1", {"name": "Reminders", "approval": mode})

    _upgrade(tmp_path)

    payload = _payload(_engine, "auto_1")
    assert "approval" not in payload
    assert payload["permission_mode"] == mode
    assert payload["name"] == "Reminders"


def test_the_default_mode_is_renamed_too(_engine, tmp_path):
    """``manual`` is the common stored value; leaving it behind would strand a key nothing
    reads, and ``AutomationStore._canonical`` writes ``permission_mode`` on the next save
    regardless — so a skipped row would carry both spellings."""
    _seed(_engine, "auto_1", {"name": "R", "approval": "manual", "always_allowed": []})

    _upgrade(tmp_path)

    payload = _payload(_engine, "auto_1")
    assert payload == {"name": "R", "permission_mode": "manual", "always_allowed": []}


def test_upgrade_is_a_no_op_when_the_key_is_absent(_engine, tmp_path):
    _seed(_engine, "auto_1", {"name": "Reminders"})

    _upgrade(tmp_path)

    assert _payload(_engine, "auto_1") == {"name": "Reminders"}


def test_one_unreadable_payload_does_not_stop_the_others(_engine, tmp_path):
    with _engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO automations (book_id, seq, id, payload) "
                "VALUES ('default', 1, 'broken', 'not json')"
            )
        )
    _seed(_engine, "auto_2", {"name": "Fine", "approval": "skip"}, seq=2)

    _upgrade(tmp_path)

    assert _payload(_engine, "auto_2")["permission_mode"] == "skip"


def test_downgrade_restores_the_old_key_exactly(_engine, tmp_path):
    original = {"name": "Reminders", "approval": "auto", "always_allowed": []}
    _seed(_engine, "auto_1", original)

    _upgrade(tmp_path)
    command.downgrade(
        make_config(f"sqlite:///{tmp_path / 'onw.db'}"),
        "202608271618_rename_automation_allowed_to_always_allowed",
    )

    assert _payload(_engine, "auto_1") == original


def test_both_renames_chain_from_the_original_shape(_engine, tmp_path):
    """0061 and 0062 rename two keys on the SAME row. A payload carrying both legacy names
    must come out carrying both new ones — the pair is one decision, split across two
    revisions only because they were asked for an hour apart."""
    command.downgrade(
        make_config(f"sqlite:///{tmp_path / 'onw.db'}"),
        "202608260009_listify_profile_identity_fields",
    )
    _seed(_engine, "auto_1", {"name": "R", "approval": "auto", "allowed": [{"src": "x"}]})

    _upgrade(tmp_path)

    payload = _payload(_engine, "auto_1")
    assert "approval" not in payload and "allowed" not in payload
    assert payload["permission_mode"] == "auto"
    assert payload["always_allowed"] == [{"src": "x"}]
