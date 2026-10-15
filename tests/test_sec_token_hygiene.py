"""H1 — the API token must not reach logs or the model, and must rotate."""

from __future__ import annotations

import importlib
import inspect
import json

import sqlalchemy as sa

from backend.core.redact import redact_token_shapes
from backend.db.auth_store import AuthStore
from backend.services import logs as logs_service

TOKEN = (
    "".join(chr(c) for c in range(65, 91)) + "abcdefghijklmnopqrst"
)  # base64url-shaped, 46 chars


def test_tail_log_masks_the_api_token_bearer_and_provider_keys(tmp_path):
    store_token = AuthStore().api_token()
    log = tmp_path / "app.log"
    log.write_text(
        "\n".join(
            [
                f"2026-10-04T10:00:00 WARNING backend.main Temporary API token: {store_token}",
                "2026-10-04T10:00:01 INFO x Authorization: Bearer abcdef0123456789",
                "2026-10-04T10:00:02 INFO x key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA",
                "2026-10-04T10:00:03 INFO x gh ghp_abcdefghijklmnopqrstuvwxyz0123",
                "2026-10-04T10:00:04 INFO x slack xoxb-1234567890-abcdefghij",
                f"2026-10-04T10:00:05 INFO x blob {TOKEN}",
                "2026-10-04T10:00:06 INFO x ordinary line about groceries",
            ]
        )
    )
    out = "\n".join(logs_service.tail_log(n=50, log_file=log))
    for secret in (
        store_token,
        "abcdef0123456789",
        "sk-ant-api03",
        "ghp_abcdef",
        "xoxb-1234",
        TOKEN,
    ):
        assert secret not in out
    assert "ordinary line about groceries" in out


def test_q_filter_cannot_probe_a_token(tmp_path):
    store_token = AuthStore().api_token()
    log = tmp_path / "app.log"
    log.write_text(f"2026-10-04T10:00:00 WARNING x token leaked {store_token}\n")
    assert logs_service.tail_log(n=10, q=store_token[:8], log_file=log) == []


def test_redaction_leaves_ordinary_text_alone():
    line = "2026-10-04 INFO backend.api GET /api/transactions 200 12ms"
    assert redact_token_shapes(line) == line


def test_large_log_walk_masks_too(tmp_path):
    log = tmp_path / "app.log"
    filler = "2026-10-04T10:00:00 INFO x " + "pad " * 10 + "\n"
    log.write_text(filler * 2000 + f"2026-10-04T10:00:01 WARNING x {TOKEN}\n")
    out = "\n".join(logs_service.tail_log(n=5, log_file=log))
    assert TOKEN not in out


def test_startup_notice_never_logs_the_token():
    from backend import main

    assert "api_token()" not in inspect.getsource(main.lifespan)


def test_migration_rotates_a_stored_token_once():
    mig = importlib.import_module("backend.db.alembic.versions.202610041200_rotate_api_token_once")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text("CREATE TABLE secret_config (namespace TEXT PRIMARY KEY, payload JSON)")
        )
        conn.execute(
            sa.text("INSERT INTO secret_config VALUES ('auth', :p)"),
            {"p": json.dumps({"api_token": "old", "password_hash": "h"})},
        )
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
        row = json.loads(conn.execute(sa.text("SELECT payload FROM secret_config")).scalar())
    assert row["api_token"] != "old" and len(row["api_token"]) >= 32
    assert row["password_hash"] == "h"
