"""Revision 202609301800 splits the one ``alert_channels`` list into per-kind routes and resolves
every price alert's ``notify`` into its own ``channels`` — each alert keeps going exactly where
it went, and nothing is linked to another setting afterwards."""

from __future__ import annotations

import json

from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config

_FROM = "202609301200_merge_topics_and_main_chains"
_TARGET = "202609301800_notification_channels_per_kind_and_per_alert"

_ALERTS = {
    "a_push": {"notify": "push"},
    "a_in_app": {"notify": "in_app"},
    "a_email": {"notify": "push_email"},
    "a_legacy": {},  # a row from before `notify` existed behaves as "push"
}


def _seed(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT OR IGNORE INTO books (id, name, base_currency, created_at) "
                "VALUES ('default', '', 'SGD', '')"
            )
        )
        wl = {"watchlists": [], "items": [], "global_config": {"alert_channels": ["telegram"]}}
        conn.execute(
            text("INSERT INTO watchlists (book_id, payload) VALUES ('default', :p)"),
            {"p": json.dumps(wl)},
        )
        for seq, (alert_id, extra) in enumerate(_ALERTS.items()):
            payload = {"id": alert_id, "ticker": "VOO", "op": ">=", "value": 1.0, **extra}
            conn.execute(
                text(
                    "INSERT INTO alerts (book_id, seq, id, payload) "
                    "VALUES ('default', :seq, :id, :p)"
                ),
                {"seq": seq, "id": alert_id, "p": json.dumps(payload)},
            )


def _state(engine) -> tuple[dict, dict]:
    with engine.connect() as conn:
        wl = json.loads(conn.execute(text("SELECT payload FROM watchlists")).scalar_one())
        alerts = {
            r[0]: json.loads(r[1]) for r in conn.execute(text("SELECT id, payload FROM alerts"))
        }
    return wl["global_config"], alerts


def test_upgrade_keeps_every_destination_and_downgrade_restores_it(tmp_path):
    db_path = tmp_path / "onw.db"
    config = make_config(f"sqlite:///{db_path}")
    command.upgrade(config, _FROM)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        _seed(engine)
        command.upgrade(config, _TARGET)
        cfg, alerts = _state(engine)
        assert "alert_channels" not in cfg
        assert cfg["notify_routes"] == {
            "price_alerts": ["telegram"],
            "watch_alerts": ["telegram"],
            "news": ["telegram"],
            "digests": ["telegram"],
        }
        assert {k: a["channels"] for k, a in alerts.items()} == {
            "a_push": ["telegram"],
            "a_in_app": [],
            "a_email": ["telegram", "email"],
            "a_legacy": ["telegram"],
        }
        assert all("notify" not in a for a in alerts.values())

        command.downgrade(config, _FROM)
        cfg, alerts = _state(engine)
        assert cfg["alert_channels"] == ["telegram"] and "notify_routes" not in cfg
        assert {k: a["notify"] for k, a in alerts.items()} == {
            "a_push": "push",
            "a_in_app": "in_app",
            "a_email": "push_email",
            "a_legacy": "push",
        }
    finally:
        engine.dispose()
