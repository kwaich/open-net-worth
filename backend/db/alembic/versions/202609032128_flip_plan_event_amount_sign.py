"""Flip the sign of Outlook life-event ``amount`` in stored plan state.

``plan.stage_whatif``'s ``addEvents`` and the Outlook's own simulation engine
(``planData.ts``) used to encode a one-off life event as ``+amount = spend, -amount =
windfall`` — backwards from every OTHER signed-amount convention in the app (a ledger
transaction, a stream, common financial intuition) and backwards from the chart's own
display, which has always shown a spend with a leading "-" and a windfall with "+". That
mismatch between what's stored and what's shown repeatedly tripped up the model itself
(observed live, session ``eccea467-e2f2-45f3-9bbf-c1615bf9e643``: it inverted the sign on a
purchase, caught its own mistake two turns later) and confused the user reading the raw
tool args. The engine and every UI surface now use the standard convention instead:
``-amount = spend, +amount = windfall``. See the accompanying frontend change
(``planData.ts``, ``outlook.tsx``, ``charts.tsx``, ``stageWhatif.ts`` doc, ``screenCommands.ts``).

This backfill negates every already-persisted event ``amount`` so a saved plan keeps its
ORIGINAL MEANING under the new convention (a stored "Home -500000" under the old scheme —
already a $500k spend — becomes "Home +500000" here, which the new engine now correctly
reads as unchanged: still a $500k spend). Without this, every existing saved baseline/
scenario event would silently flip meaning (an existing spend would start being simulated
as a windfall) the moment the new frontend build shipped.

``plan_state`` is a per-book singleton (PK ``book_id``, see ``db/tables.py``); ``payload`` is
``{"scenarios": [...], "baseline": {...} | None, "budget"?: {...}}`` stored verbatim
(``db/plan_state_store.py``). Events live at ``payload.baseline.events[]`` and at
``payload.scenarios[*].events[]``. Guaranteed-income streams (``events``' sibling ``streams``
key) are untouched — they were never signed (always income, never spend), so nothing about
them changes.

Revision ID: 202609032128_flip_plan_event_amount_sign
Revises: 202609021600_rejected_identities
Create Date: 2026-09-03
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

revision = "202609032128_flip_plan_event_amount_sign"
down_revision = "202609021600_rejected_identities"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def _flip_events(events: object) -> tuple[list, bool]:
    """Negate ``amount`` on every well-formed event. Returns (events, changed)."""
    if not isinstance(events, list):
        return events if isinstance(events, list) else [], False
    changed = False
    out = []
    for ev in events:
        if isinstance(ev, dict) and isinstance(ev.get("amount"), (int, float)):
            ev = {**ev, "amount": -ev["amount"]}
            changed = True
        out.append(ev)
    return out, changed


def _flip_payload(data: dict) -> bool:
    """Negate event amounts in-place across baseline + every scenario. Returns whether
    anything changed."""
    changed = False
    baseline = data.get("baseline")
    if isinstance(baseline, dict):
        events, ev_changed = _flip_events(baseline.get("events"))
        if ev_changed:
            baseline["events"] = events
            changed = True

    scenarios = data.get("scenarios")
    if isinstance(scenarios, list):
        for scen in scenarios:
            if not isinstance(scen, dict):
                continue
            events, ev_changed = _flip_events(scen.get("events"))
            if ev_changed:
                scen["events"] = events
                changed = True

    return changed


def _run(conn, negate_again: bool) -> None:
    rows = conn.execute(sa.text("SELECT book_id, payload FROM plan_state")).fetchall()
    flipped = 0
    for book_id, payload in rows:
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if _flip_payload(data):
            conn.execute(
                sa.text("UPDATE plan_state SET payload = :p WHERE book_id = :b"),
                {"p": json.dumps(data), "b": book_id},
            )
            flipped += 1
    verb = "re-flipped" if negate_again else "flipped"
    logger.info("plan_state event amount sign: %s %d book row(s)", verb, flipped)


def upgrade() -> None:
    _run(op.get_bind(), negate_again=False)


def downgrade() -> None:
    """Negating twice is the identity — flip is its own inverse."""
    _run(op.get_bind(), negate_again=True)
