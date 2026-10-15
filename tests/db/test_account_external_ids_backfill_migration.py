"""202609051210_account_external_ids_from_config_maps — moves every provider
``account_map`` entry onto its account as ``external_ids`` and strips the four retired
config keys (RFC connectors-as-data-sources §D, AC5).

Fully synthetic in-memory SQLite (mirrors tests/test_external_ref_emitter.py's own
``_migration_db`` pattern) — never a real data dir, never `op.get_bind()`'s live engine.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import sqlalchemy as sa


def _load_migration():
    """The revision's module name starts with a digit, so it cannot be imported by name."""
    path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "backend/db/alembic/versions/202609051210_account_external_ids_from_config_maps.py"
    )
    spec = importlib.util.spec_from_file_location("_backfill_external_ids_rev", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backfill = _load_migration()


def _migration_db(configs: dict[str, dict], accounts: dict[str, dict]):
    engine = sa.create_engine("sqlite://")
    conn = engine.connect()
    conn.execute(sa.text("CREATE TABLE secret_config (namespace TEXT PRIMARY KEY, payload TEXT)"))
    conn.execute(sa.text("CREATE TABLE accounts (id TEXT PRIMARY KEY, payload TEXT)"))
    for namespace, payload in configs.items():
        conn.execute(
            sa.text("INSERT INTO secret_config VALUES (:n, :p)"),
            {"n": namespace, "p": json.dumps(payload)},
        )
    for account_id, payload in accounts.items():
        conn.execute(
            sa.text("INSERT INTO accounts VALUES (:i, :p)"),
            {"i": account_id, "p": json.dumps(payload)},
        )
    return conn


def _config(conn, namespace: str) -> dict:
    row = conn.execute(
        sa.text("SELECT payload FROM secret_config WHERE namespace = :n"), {"n": namespace}
    ).scalar_one()
    return json.loads(row)


def _account(conn, account_id: str) -> dict:
    row = conn.execute(
        sa.text("SELECT payload FROM accounts WHERE id = :i"), {"i": account_id}
    ).scalar_one()
    return json.loads(row)


def test_upgrade_links_the_account_and_strips_retired_keys(monkeypatch):
    conn = _migration_db(
        configs={
            "plaid_config": {
                "account_map": {"plaid-acc-1": "acc_1"},
                "poll_interval_seconds": 21600,
                "cursor": "abc",
                "enabled": True,
            }
        },
        accounts={"acc_1": {"id": "acc_1", "name": "Checking", "external_ids": []}},
    )
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()

    account = _account(conn, "acc_1")
    assert account["external_ids"] == [
        {
            "source": "plaid",
            "ref": "plaid-acc-1",
            "name": "",
            "mask": "",
            "linked_at": account["external_ids"][0]["linked_at"],
        }
    ]
    config = _config(conn, "plaid_config")
    assert "account_map" not in config
    assert "poll_interval_seconds" not in config
    assert "cursor" not in config
    assert config["enabled"] is True  # untouched, non-retired key survives


def test_upgrade_skips_a_pair_whose_account_no_longer_exists(monkeypatch):
    conn = _migration_db(
        configs={"plaid_config": {"account_map": {"plaid-acc-1": "acc_gone"}}},
        accounts={},
    )
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()  # must not raise

    config = _config(conn, "plaid_config")
    assert "account_map" not in config  # still stripped — nothing left to read it


def test_upgrade_is_idempotent(monkeypatch):
    conn = _migration_db(
        configs={"plaid_config": {"account_map": {"plaid-acc-1": "acc_1"}}},
        accounts={"acc_1": {"id": "acc_1", "external_ids": []}},
    )
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()
    first = _account(conn, "acc_1")["external_ids"]
    backfill.upgrade()
    second = _account(conn, "acc_1")["external_ids"]

    assert first == second
    assert len(second) == 1  # never double-appended


def test_upgrade_handles_multiple_providers_and_pairs(monkeypatch):
    conn = _migration_db(
        configs={
            "plaid_config": {"account_map": {"p1": "acc_1"}},
            "simplefin_config": {"account_map": {"s1": "acc_1", "s2": "acc_2"}},
        },
        accounts={
            "acc_1": {"id": "acc_1", "external_ids": []},
            "acc_2": {"id": "acc_2", "external_ids": []},
        },
    )
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()

    acc1_sources = {(e["source"], e["ref"]) for e in _account(conn, "acc_1")["external_ids"]}
    acc2_sources = {(e["source"], e["ref"]) for e in _account(conn, "acc_2")["external_ids"]}
    assert acc1_sources == {("plaid", "p1"), ("simplefin", "s1")}
    assert acc2_sources == {("simplefin", "s2")}


def test_upgrade_preserves_an_existing_link_on_the_account(monkeypatch):
    """A hand-linked external_ids entry already on the account is never duplicated."""
    conn = _migration_db(
        configs={"plaid_config": {"account_map": {"p1": "acc_1"}}},
        accounts={
            "acc_1": {
                "id": "acc_1",
                "external_ids": [
                    {"source": "plaid", "ref": "p1", "name": "", "mask": "", "linked_at": "t0"}
                ],
            }
        },
    )
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()

    assert _account(conn, "acc_1")["external_ids"] == [
        {"source": "plaid", "ref": "p1", "name": "", "mask": "", "linked_at": "t0"}
    ]


def test_downgrade_reconstructs_account_map_and_unlinks(monkeypatch):
    conn = _migration_db(
        configs={"plaid_config": {"account_map": {"p1": "acc_1"}}},
        accounts={"acc_1": {"id": "acc_1", "external_ids": []}},
    )
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()
    backfill.downgrade()

    assert _config(conn, "plaid_config")["account_map"] == {"p1": "acc_1"}
    assert _account(conn, "acc_1")["external_ids"] == []


def test_downgrade_does_not_restore_the_retired_cursor_fields(monkeypatch):
    """Documented non-losslessness (module docstring): only the account_map half undoes."""
    conn = _migration_db(
        configs={
            "plaid_config": {
                "account_map": {"p1": "acc_1"},
                "cursor": "abc",
                "poll_interval_seconds": 21600,
            }
        },
        accounts={"acc_1": {"id": "acc_1", "external_ids": []}},
    )
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()
    backfill.downgrade()

    config = _config(conn, "plaid_config")
    assert "cursor" not in config
    assert "poll_interval_seconds" not in config
    assert config["account_map"] == {"p1": "acc_1"}


def test_no_ops_cleanly_when_no_provider_configs_exist(monkeypatch):
    conn = _migration_db(configs={}, accounts={"acc_1": {"id": "acc_1", "external_ids": []}})
    monkeypatch.setattr(backfill.op, "get_bind", lambda: conn)

    backfill.upgrade()  # must not raise

    assert _account(conn, "acc_1")["external_ids"] == []
