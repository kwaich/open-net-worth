"""Remove owned-asset review scheduling and type legacy insurance/maturity facts.

Revision ID: 202609101600_remove_owned_asset_reviews
Revises: 202609091200_statement_parse_progress
Create Date: 2026-09-10
"""

from __future__ import annotations

import json
from datetime import date, datetime

import sqlalchemy as sa
from alembic import op

revision = "202609101600_remove_owned_asset_reviews"
down_revision = "202609091200_statement_parse_progress"
branch_labels = None
depends_on = None

_SELECT_ASSETS = sa.text("SELECT id, payload FROM owned_assets")
_SELECT_CATEGORIES = sa.text("SELECT id, payload FROM owned_asset_categories")
_SELECT_AUTOMATIONS = sa.text("SELECT id, payload FROM automations")
_UPDATE_ASSET = sa.text("UPDATE owned_assets SET payload = :payload WHERE id = :id")
_UPDATE_CATEGORY = sa.text("UPDATE owned_asset_categories SET payload = :payload WHERE id = :id")
_DELETE_AUTOMATION = sa.text("DELETE FROM automations WHERE id = :id")
_DELETE_RUNTIME = sa.text("DELETE FROM automation_runtime WHERE id = :id")
_FALSE_INSURED = {"no", "false", "not insured", "uninsured", "none"}
_PLAIN_INSURED = {"yes", "true", "insured"}


def _dict(raw, row_id: str) -> dict:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"payload {row_id!r} is unreadable; migration refused") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"payload {row_id!r} must be a JSON object; migration refused")
    return payload


def _legacy_date(value: str) -> str | None:
    text = value.strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    for pattern in ("%Y-%m", "%b %Y", "%B %Y"):
        try:
            return datetime.strptime(text, pattern).date().replace(day=1).isoformat()
        except ValueError:
            continue
    return None


def _rewrite_facts(payload: dict) -> None:
    insured = payload.get("insured") if isinstance(payload.get("insured"), bool) else False
    maturity = payload.get("maturity_date") or None
    rewritten: list[list[str]] = []
    for fact in payload.get("facts") or []:
        if not isinstance(fact, (list, tuple)) or len(fact) != 2:
            continue
        label, value = str(fact[0]), str(fact[1])
        key = label.strip().lower().replace("’", "'")
        detail = value.strip()
        if key == "insured":
            normalized = detail.lower()
            insured = bool(detail) and normalized not in _FALSE_INSURED
            if insured and normalized not in _PLAIN_INSURED:
                rewritten.append(["Insurance details", detail])
        elif key == "matures":
            parsed = _legacy_date(detail)
            if parsed:
                maturity = parsed
            elif detail:
                rewritten.append(["Maturity (legacy text)", detail])
        elif key in {"where is it kept", "where it's kept"}:
            rewritten.append(["Where it’s kept", detail])
        else:
            rewritten.append([label, value])
    payload["insured"] = insured
    payload["maturity_date"] = maturity
    payload["facts"] = rewritten


def _rewrite_assets(conn) -> None:
    for row_id, raw in conn.execute(_SELECT_ASSETS).fetchall():
        payload = _dict(raw, row_id)
        for key in ("cadence_months", "automation_id", "months_old", "automation"):
            payload.pop(key, None)
        _rewrite_facts(payload)
        conn.execute(_UPDATE_ASSET, {"id": row_id, "payload": json.dumps(payload)})


def _rewrite_categories(conn) -> None:
    for row_id, raw in conn.execute(_SELECT_CATEGORIES).fetchall():
        payload = _dict(raw, row_id)
        payload.pop("cadence_months", None)
        conn.execute(_UPDATE_CATEGORY, {"id": row_id, "payload": json.dumps(payload)})


def _delete_asset_review_automations(conn) -> None:
    for row_id, raw in conn.execute(_SELECT_AUTOMATIONS).fetchall():
        payload = _dict(raw, row_id)
        system_key = str((payload.get("provenance") or {}).get("system_key") or "")
        if system_key.startswith("asset_remark:"):
            conn.execute(_DELETE_RUNTIME, {"id": row_id})
            conn.execute(_DELETE_AUTOMATION, {"id": row_id})


def upgrade() -> None:
    conn = op.get_bind()
    _rewrite_assets(conn)
    _rewrite_categories(conn)
    _delete_asset_review_automations(conn)


def downgrade() -> None:
    """Restore old-code placeholders; deleted schedules and original fact text are lossy."""
    conn = op.get_bind()
    for row_id, raw in conn.execute(_SELECT_ASSETS).fetchall():
        payload = _dict(raw, row_id)
        facts = list(payload.get("facts") or [])
        facts.append(["Insured", "Yes" if payload.pop("insured", False) else "No"])
        maturity = payload.pop("maturity_date", None)
        if maturity:
            facts.append(["Matures", maturity])
        payload["facts"] = facts
        payload["cadence_months"] = 12
        payload["automation_id"] = None
        conn.execute(_UPDATE_ASSET, {"id": row_id, "payload": json.dumps(payload)})
    for row_id, raw in conn.execute(_SELECT_CATEGORIES).fetchall():
        payload = _dict(raw, row_id)
        payload["cadence_months"] = 12
        conn.execute(_UPDATE_CATEGORY, {"id": row_id, "payload": json.dumps(payload)})
