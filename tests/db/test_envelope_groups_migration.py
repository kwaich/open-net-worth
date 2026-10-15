"""Real migration coverage for ``202610031200_envelope_groups_not_category_groups``: the table
rename, the membership strip, and the exact deletion rule — including every "keep" side.

Convention mirrors tests/db/test_data_hygiene_cleanup_migration.py: migrate a fresh sqlite file
to the revision right before this one, seed rows with a PLAIN ``create_engine`` connection, then
upgrade and assert on the resulting rows.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from backend.db.alembic_config import make_config
from backend.db.tables import DEFAULT_BOOK_ID

_FROM = "202610011200_desktop_access_and_ingest"
_TARGET = "202610031200_envelope_groups_not_category_groups"
_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "backend/db/alembic/versions" / f"{_TARGET}.py"
)
_B = DEFAULT_BOOK_ID


@pytest.fixture
def legacy_db(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _FROM)
    engine = create_engine(url)
    yield engine, url
    engine.dispose()


def _insert(conn, table: str, seq: int, row_id: str, payload: dict) -> None:
    conn.execute(
        text(f"INSERT INTO {table} (book_id, seq, id, payload) VALUES (:b, :s, :id, :p)"),
        {"b": _B, "s": seq, "id": row_id, "p": json.dumps(payload)},
    )


def _group(row_id: str, name: str, categories: list[str], **extra) -> dict:
    return {
        "id": row_id,
        "name": name,
        "categories": categories,
        "collapsed": False,
        "order": 0,
        "is_others": False,
        **extra,
    }


def _seed(conn) -> None:
    _insert(conn, "categories", 0, "cat_exp", {"id": "cat_exp", "label": "Food", "kind": "expense"})
    _insert(conn, "categories", 1, "cat_inc", {"id": "cat_inc", "label": "Pay", "kind": "income"})
    groups = [
        _group("grp_used", "Bills", ["cat_exp"]),
        _group("grp_others", "Others", [], is_others=True),
        _group("grp_ccp", "Credit Card Payments", ["cat_inc"]),
        _group("grp_leak", "__income__", ["cat_inc"]),
        _group("grp_leak_free", "__income__", ["cat_inc"]),
        _group("__income__", "Income", ["cat_inc"]),
        _group("grp_incomeonly", "Salary", ["cat_inc", "cat_dead"]),
        _group("grp_incomeref", "Inflow", ["cat_inc"]),
        _group("grp_empty", "Fun", []),
        _group("grp_spending_members", "Hobbies", ["cat_exp"]),
        _group("grp_mixed", "Misc", ["cat_exp", "cat_inc"]),
        _group("grp_legacy_ids", "Legacy", [], envelope_ids=["env_x"]),
    ]
    for seq, g in enumerate(groups):
        _insert(conn, "category_groups", seq, g["id"], g)
    _insert(conn, "envelopes", 0, "env_1", {"id": "env_1", "name": "Rent", "group_id": "grp_used"})
    _insert(
        conn, "envelopes", 1, "env_2", {"id": "env_2", "name": "Odd", "group_id": "grp_incomeref"}
    )
    _insert(
        conn,
        "envelopes",
        2,
        "env_arch",
        {"id": "env_arch", "name": "Old", "group_id": "grp_leak", "archived": True},
    )
    conn.execute(
        text(
            "INSERT INTO write_log (checkpoint_id, seq, table_name, pk, before_payload) "
            "VALUES ('ck1', 0, 'category_groups', 'grp_used', :p), "
            "('ck1', 1, 'category_groups', 'grp_new', NULL)"
        ),
        {"p": json.dumps(_group("grp_used", "Bills", ["cat_exp"]))},
    )


def _group_rows(conn) -> dict[str, dict]:
    rows = conn.execute(text("SELECT id, payload FROM envelope_groups")).fetchall()
    return {r.id: json.loads(r.payload) if isinstance(r.payload, str) else r.payload for r in rows}


def test_the_table_is_renamed_and_membership_stripped_from_every_surviving_group(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _seed(conn)

    command.upgrade(make_config(url), "head")

    with engine.connect() as conn:
        names = inspect(conn).get_table_names()
        assert "envelope_groups" in names and "category_groups" not in names
        index_names = {i["name"] for i in inspect(conn).get_indexes("envelope_groups")}
        assert "ix_envelope_groups_book_seq" in index_names
        groups = _group_rows(conn)
    for payload in groups.values():
        assert "categories" not in payload and "envelope_ids" not in payload
    assert groups["grp_used"] == {
        "id": "grp_used",
        "name": "Bills",
        "collapsed": False,
        "order": 0,
        "is_others": False,
    }


def test_the_deletion_rule_removes_only_unreferenced_sentinel_or_income_only_groups(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _seed(conn)

    command.upgrade(make_config(url), "head")

    with engine.connect() as conn:
        survivors = set(_group_rows(conn))
    assert survivors == {
        "grp_used",  # an envelope points at it
        "grp_others",  # the catch-all
        "grp_ccp",  # an app-kept group, even though its old list was income-only
        "grp_incomeref",  # income-only, but a live envelope points at it
        "grp_leak",  # a sentinel name, but an (archived) envelope still points at it
        "grp_empty",  # a user's own empty spending group
        "grp_spending_members",  # no envelopes, but its members are spending categories
        "grp_mixed",  # spending + income members: not income-only
        "grp_legacy_ids",
    }
    # Gone: the unreferenced "__income__" NAME, the "__income__" ID, and the unreferenced
    # income-only group (its dead category id is ignored, so it still counts as income-only).


def test_write_log_before_images_follow_the_rename_and_lose_the_membership(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _seed(conn)

    command.upgrade(make_config(url), "head")

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT table_name, pk, before_payload FROM write_log ORDER BY seq")
        ).fetchall()
    assert [r.table_name for r in rows] == ["envelope_groups", "envelope_groups"]
    first = json.loads(rows[0].before_payload)
    assert first["name"] == "Bills" and "categories" not in first
    assert rows[1].before_payload is None


def test_rerunning_the_migration_changes_nothing(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _seed(conn)
    command.upgrade(make_config(url), "head")

    spec = importlib.util.spec_from_file_location("_envelope_groups_rev", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        before = _group_rows(conn)
        assert module._run(conn) == {}
        assert _group_rows(conn) == before


def test_a_book_with_no_groups_migrates_cleanly(legacy_db):
    engine, url = legacy_db

    command.upgrade(make_config(url), "head")

    with engine.connect() as conn:
        assert "envelope_groups" in inspect(conn).get_table_names()
        assert _group_rows(conn) == {}


def test_downgrade_renames_the_table_back(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _seed(conn)
    command.upgrade(make_config(url), _TARGET)

    command.downgrade(make_config(url), _FROM)

    with engine.connect() as conn:
        names = inspect(conn).get_table_names()
        assert "category_groups" in names and "envelope_groups" not in names
        indexes = {i["name"] for i in inspect(conn).get_indexes("category_groups")}
        assert "ix_category_groups_book_seq" in indexes
