"""alerts_service — the single choke point for standalone price alerts (ADR 0014).

Import-pure (services → db → core). All alert mutations flow through
``apply_alert_change`` so the JsonStore write hook (checkpoint snapshot) fires once
per change, mirroring the other ``apply_*_change`` choke points. ai/tools call the
``alert_store()`` factory rather than importing db directly.
"""

from __future__ import annotations

import logging

from backend.core.errors import NotFoundError, ValidationError
from backend.core.write_receipt import record_write
from backend.db.alert_store import AlertStore, norm_op, norm_value
from backend.services import notify_routes

logger = logging.getLogger(__name__)


def alert_store() -> AlertStore:
    return AlertStore()


# ── Reads ──────────────────────────────────────────────────────────────────────


def list_alerts(ticker: str | None = None) -> list[dict]:
    store = alert_store()
    return store.for_ticker(ticker) if ticker else store.all()


def alerts_by_ticker() -> dict[str, list[dict]]:
    """{UPPER_ticker: [alert,...]} — the shape the monitor alert lane consumes."""
    return alert_store().by_ticker()


# ── Write choke point ────────────────────────────────────────────────────────


def apply_alert_change(change: dict) -> dict:
    """The ONLY alert mutation entry point. change = {type: create|update|delete, ...}."""
    kind = change.get("type")
    store = alert_store()
    if "channels" in change and change["channels"] is not None:
        change = {**change, "channels": notify_routes.normalize_channels(change["channels"])}
    if kind == "create":
        if change.get("channels") is None:
            # A new alert starts from the user's price-alert default, then owns its channels.
            change = {**change, "channels": notify_routes.channels_for(notify_routes.PRICE_ALERTS)}
        created = store.create(change)
        _record(created.get("id") or "", "create")
        return created
    if kind == "update":
        alert_id = change.get("id") or ""
        updated = store.update(alert_id, change)
        if updated is None:
            raise NotFoundError(f"alert {alert_id!r} not found")
        _record(updated.get("id") or alert_id, "update")
        return updated
    if kind == "delete":
        alert_id = change.get("id") or ""
        if not store.delete(alert_id):
            raise NotFoundError(f"alert {alert_id!r} not found")
        _record(alert_id, "delete")
        return {"id": alert_id, "deleted": True}
    raise ValidationError(f"unknown alert change type {kind!r}")


def _record(alert_id: str, op: str) -> None:
    """Report this alert write onto the turn's receipt (a no-op outside an assistant turn).
    Never raises — a mechanism that only DESCRIBES a write must not be able to fail it."""
    try:
        record_write(op, alert_id, entity_type="alert")
    except Exception:  # noqa: BLE001
        pass


# ── One-time migration: lift embedded watchlist price_alerts into the store ──────


def _coerce_condition(cond: object) -> tuple[str, float]:
    """Normalize a stored alert condition to (op, value).

    Accepts the canonical {op, value, note} and the dead-but-possible legacy
    {direction: above|below, target} shape (only ever seen in the example fixture)."""
    if not isinstance(cond, dict):
        raise ValidationError("price_alert condition is not an object")
    if cond.get("op") is not None and cond.get("value") is not None:
        return norm_op(cond["op"]), norm_value(cond["value"])
    direction = cond.get("direction")
    if direction in ("above", "below") and cond.get("target") is not None:
        return (">=" if direction == "above" else "<="), norm_value(cond["target"])
    raise ValidationError(f"unrecognized price_alert shape: {sorted(cond)}")
