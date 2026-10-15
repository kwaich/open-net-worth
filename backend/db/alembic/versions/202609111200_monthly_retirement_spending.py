"""Store retirement spending monthly with explicit, unambiguous field names.

Profile ``fire_annual_expenses`` and Outlook plan-state ``spend`` both held annual
retirement spending despite names and tool descriptions that repeatedly disagreed about
their unit. Rewrite them to ``fire_monthly_retirement_spending`` and
``monthlyRetirementSpending``, dividing numeric values by 12 so every existing plan keeps
the same meaning. Runtime code reads only the new fields; this migration is the compatibility
boundary.

Revision ID: 202609111200_monthly_retirement_spending
Revises: 202609101600_remove_owned_asset_reviews
Create Date: 2026-09-11
"""

from __future__ import annotations

import json
import logging
import math

import sqlalchemy as sa
from alembic import op

revision = "202609111200_monthly_retirement_spending"
down_revision = "202609101600_remove_owned_asset_reviews"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def _convert_key(payload: dict, old: str, new: str, factor: float) -> bool:
    if old not in payload:
        return False
    value = payload[old]
    if value is None:
        converted = None
    else:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise RuntimeError(f"{old!r} must be a finite number or null; migration refused")
        converted = value * factor
        if not math.isfinite(converted):
            raise RuntimeError(f"converted {old!r} must be finite; migration refused")
    if new in payload:
        destination = payload[new]
        if destination is not None and (
            isinstance(destination, bool)
            or not isinstance(destination, (int, float))
            or not math.isfinite(destination)
        ):
            raise RuntimeError(f"{new!r} must be a finite number or null; migration refused")
        if destination != converted:
            raise RuntimeError(f"conflicting {old!r} and {new!r}; migration refused")
    else:
        payload[new] = converted
    payload.pop(old)
    return True


def _rewrite_plan(payload: dict, old: str, new: str, factor: float) -> bool:
    changed = False
    baseline = payload.get("baseline")
    if isinstance(baseline, dict):
        changed = _convert_key(baseline, old, new, factor) or changed
    scenarios = payload.get("scenarios")
    if isinstance(scenarios, list):
        for scenario in scenarios:
            if isinstance(scenario, dict):
                changed = _convert_key(scenario, old, new, factor) or changed
    return changed


def _load(raw: object, table: str, book_id: str) -> dict:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{table} payload for book {book_id!r} is unreadable") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{table} payload for book {book_id!r} must be an object")
    return payload


def _run(*, downgrade: bool) -> None:
    conn = op.get_bind()
    factor = 12.0 if downgrade else 1 / 12.0
    profile_old, profile_new = (
        ("fire_monthly_retirement_spending", "fire_annual_expenses")
        if downgrade
        else ("fire_annual_expenses", "fire_monthly_retirement_spending")
    )
    plan_old, plan_new = (
        ("monthlyRetirementSpending", "spend")
        if downgrade
        else ("spend", "monthlyRetirementSpending")
    )
    touched = 0
    for book_id, raw in conn.execute(sa.text("SELECT book_id, payload FROM profile")):
        payload = _load(raw, "profile", book_id)
        if _convert_key(payload, profile_old, profile_new, factor):
            conn.execute(
                sa.text("UPDATE profile SET payload = :payload WHERE book_id = :book_id"),
                {"payload": json.dumps(payload), "book_id": book_id},
            )
            touched += 1
    for book_id, raw in conn.execute(sa.text("SELECT book_id, payload FROM plan_state")):
        payload = _load(raw, "plan_state", book_id)
        if _rewrite_plan(payload, plan_old, plan_new, factor):
            conn.execute(
                sa.text("UPDATE plan_state SET payload = :payload WHERE book_id = :book_id"),
                {"payload": json.dumps(payload), "book_id": book_id},
            )
            touched += 1
    logger.info("monthly retirement spending migration rewrote %d row(s)", touched)


def upgrade() -> None:
    _run(downgrade=False)


def downgrade() -> None:
    _run(downgrade=True)
