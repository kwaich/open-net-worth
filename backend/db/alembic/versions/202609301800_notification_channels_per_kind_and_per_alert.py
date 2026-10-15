"""Split the one ``alert_channels`` list into per-kind routes, and give each price alert its own.

Before: one ``global_config.alert_channels`` list fed every external push, and a price alert's
``notify`` (``push`` / ``in_app`` / ``push_email``) only chose "that list", "nothing", or "that
list + email" — so the alert's destination silently followed a setting on another screen.

After (ADR ``2026-09-30-notification-channels-per-kind-and-per-alert``):
  * ``global_config.notify_routes = {price_alerts, watch_alerts, news, digests}``, each seeded
    with the old list, so every kind keeps going exactly where it went.
  * each alert's ``notify`` becomes its own ``channels``, resolved ONCE against its book's old
    list: ``in_app`` → ``[]``; ``push`` → the list; ``push_email`` → the list + ``email``.

Same payload-rewrite shape as ``202609291200_automation_completed_status``: an unreadable
payload is skipped, never fails the boot. ``downgrade()`` restores ``alert_channels`` from the
price-alert route and maps ``channels`` back (``[]`` → ``in_app``, anything else → ``push``,
``+email`` beyond the list → ``push_email``).

Revision ID: 202609301800_notification_channels_per_kind_and_per_alert
Revises: 202609301200_merge_topics_and_main_chains
Create Date: 2026-09-30
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.notification_channels")

revision = "202609301800_notification_channels_per_kind_and_per_alert"
down_revision = "202609301200_merge_topics_and_main_chains"
branch_labels = None
depends_on = None

_KINDS = ("price_alerts", "watch_alerts", "news", "digests")


def _load(raw):
    return json.loads(raw) if isinstance(raw, str) else raw


def _rows(conn, table: str):
    for key, row_id, raw in conn.execute(
        sa.text(f"SELECT book_id, {'id' if table == 'alerts' else 'book_id'}, payload FROM {table}")
    ).fetchall():
        try:
            payload = _load(raw)
        except Exception:  # noqa: BLE001 — one unreadable row must not stop the rest
            log.warning("%s payload %s is unreadable; skipped", table, row_id)
            continue
        if isinstance(payload, dict):
            yield key, row_id, payload


def _save(conn, table: str, row_id, payload: dict) -> None:
    key = "id" if table == "alerts" else "book_id"
    conn.execute(
        sa.text(f"UPDATE {table} SET payload = :payload WHERE {key} = :id"),
        {"payload": json.dumps(payload), "id": row_id},
    )


def _clean(ids) -> list[str]:
    out: list[str] = []
    for c in ids or []:
        if isinstance(c, str) and c and c != "in_app" and c not in out:
            out.append(c)
    return out


def upgrade() -> None:
    conn = op.get_bind()
    book_channels: dict[str, list[str]] = {}
    for book_id, row_id, payload in _rows(conn, "watchlists"):
        config = payload.get("global_config")
        # A config that never stored the old list is left byte-identical: the store's defaults
        # already give every kind [] — the same "in-app only" the missing list meant.
        if not isinstance(config, dict) or "alert_channels" not in config:
            continue
        old = _clean(config.pop("alert_channels"))
        book_channels[book_id] = old
        if not isinstance(config.get("notify_routes"), dict):
            config["notify_routes"] = {kind: list(old) for kind in _KINDS}
        _save(conn, "watchlists", row_id, payload)

    for book_id, row_id, payload in _rows(conn, "alerts"):
        old = book_channels.get(book_id, [])
        if "notify" not in payload and ("channels" in payload or not old):
            continue  # already migrated, or a legacy row whose "push" reached nothing anyway
        notify = payload.pop("notify", None) or "push"
        if notify == "in_app":
            channels: list[str] = []
        elif notify == "push_email":
            channels = _clean([*old, "email"])
        else:
            channels = list(old)
        payload["channels"] = channels
        _save(conn, "alerts", row_id, payload)


def downgrade() -> None:
    conn = op.get_bind()
    book_channels: dict[str, list[str]] = {}
    for book_id, row_id, payload in _rows(conn, "watchlists"):
        config = payload.get("global_config")
        if not isinstance(config, dict) or "notify_routes" not in config:
            continue
        routes = config.pop("notify_routes") or {}
        old = _clean(routes.get("price_alerts"))
        config["alert_channels"] = old
        book_channels[book_id] = old
        _save(conn, "watchlists", row_id, payload)

    for book_id, row_id, payload in _rows(conn, "alerts"):
        if "channels" not in payload:
            continue
        channels = _clean(payload.pop("channels"))
        old = book_channels.get(book_id, [])
        if not channels:
            payload["notify"] = "in_app"
        elif "email" in channels and "email" not in old:
            payload["notify"] = "push_email"
        else:
            payload["notify"] = "push"
        _save(conn, "alerts", row_id, payload)
