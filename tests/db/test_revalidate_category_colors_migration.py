"""``202610041300_revalidate_stored_category_colors`` resets non-hex swatch colours to null."""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config
from backend.db.tables import DEFAULT_BOOK_ID

_FROM = "202610041200_rotate_api_token_once"
_TARGET = "202610041300_revalidate_stored_category_colors"


@pytest.fixture
def legacy_db(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _FROM)
    engine = create_engine(url)
    yield engine, url
    engine.dispose()


def _insert(conn, table, seq, row_id, payload):
    conn.execute(
        text(f"INSERT INTO {table} (book_id, seq, id, payload) VALUES (:b, :s, :id, :p)"),
        {"b": DEFAULT_BOOK_ID, "s": seq, "id": row_id, "p": json.dumps(payload)},
    )


def _color(conn, table, row_id):
    raw = conn.execute(text(f"SELECT payload FROM {table} WHERE id = :id"), {"id": row_id}).scalar()
    return json.loads(raw).get("color", "<absent>")


def test_invalid_colours_reset_valid_ones_kept(legacy_db):
    engine, url = legacy_db
    xss = '"><img src=x onerror=alert(1)>'
    with engine.begin() as conn:
        _insert(conn, "categories", 0, "cat_ok", {"id": "cat_ok", "color": "#3B86dd"})
        _insert(conn, "categories", 1, "cat_xss", {"id": "cat_xss", "color": xss})
        _insert(conn, "categories", 2, "cat_blank", {"id": "cat_blank", "color": ""})
        _insert(conn, "categories", 3, "cat_none", {"id": "cat_none"})
        _insert(conn, "envelopes", 0, "env_xss", {"id": "env_xss", "color": "red;x:y"})
    command.upgrade(make_config(url), _TARGET)
    with engine.connect() as conn:
        assert _color(conn, "categories", "cat_ok") == "#3B86dd"
        assert _color(conn, "categories", "cat_xss") is None
        assert _color(conn, "categories", "cat_blank") == ""
        assert _color(conn, "categories", "cat_none") == "<absent>"
        assert _color(conn, "envelopes", "env_xss") is None
