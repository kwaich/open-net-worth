"""
equity_report_service — per-year vest report (feat/equity-comp).

Extracted from ``services/equity.py`` to keep each file ≤400 lines (CLAUDE.md).
This is a pure REPORTING layer over the vest-event store + the existing FX
infrastructure. It does NOT mutate anything.

``equity_report(year)`` returns the per-vest table for a year + the total gross gain.
Deferred-grant events appear in the table (flagged via ``tax_event``) but are excluded
from the total, because a deferred grant is not taxed at vest — a statement about tax
treatment, not about the ledger. NO vest posts a transaction any more (ADR
2026-09-22-a-vest-books-no-income-transaction); this report reads the vest EVENT's own
facts, which is what makes it the surviving record of vest compensation.

Base amounts are RECOMPUTED from the native leg + current FX via ``txn_base_amount`` /
``to_base`` — the stored ``base_amount`` snapshot is never trusted (CLAUDE.md invariant).

Imports are downward/sibling only (services + core) — no api/ai (Contract 3).
"""

from __future__ import annotations

from backend.core.fx import base_currency, to_base
from backend.core.user_time import user_today
from backend.db.grant_store import GrantStore
from backend.db.instrument_store import InstrumentStore
from backend.db.transaction_store import TransactionStore
from backend.db.vest_event_store import VestEventStore

grants = GrantStore()
vest_events = VestEventStore()
instruments = InstrumentStore()
transactions = TransactionStore()


def equity_report(year: int | None = None) -> dict:
    """Per-vest report for a year.

    Returns:
        {
          year, years, base_currency,
          events: [{id, grant_id, symbol, grant_date, vest_date,
                    price_paid_per_unit, fmv_per_unit, fmv_currency,
                    units, gross_gain_base, tax_event}],
          total_gain_base,
        }

    ``years`` = all years with ≥1 vest event (for the frontend year picker — no extra endpoint).
    """
    from backend.services import lots as lots_service

    base = base_currency()
    rates = lots_service._load_rates()

    # Derive all years with vest events.
    all_events = vest_events.all()
    years_with_events = sorted(
        {ev.get("date", "")[:4] for ev in all_events if ev.get("date")},
        reverse=True,
    )

    target_year = int(year) if year else user_today().year
    date_from = f"{target_year}-01-01"
    date_to = f"{target_year}-12-31"

    period_events = vest_events.query_events(date_from=date_from, date_to=date_to)
    grant_map = {g["id"]: g for g in grants.all()}
    inst_map = {i["id"]: i for i in instruments.all()}

    event_rows = []
    total_gain_base = 0.0

    for ev in period_events:
        grant = grant_map.get(ev.get("grant_id", "")) or {}
        inst = inst_map.get(grant.get("instrument_id", "")) or {}
        symbol = inst.get("symbol") or inst.get("name") or ""
        fmv_ccy = (ev.get("fmv_currency") or base).upper()
        units = float(ev.get("units") or 0)
        fmv = float(ev.get("fmv_per_unit") or 0)
        price_paid = float(ev.get("price_paid_per_unit") or 0)
        gross_gain_native = round(units * (fmv - price_paid), 2)
        gross_gain_base = round(to_base(gross_gain_native, fmv_ccy, base, rates=rates), 2)

        tax_event = grant.get("tax_event", "at_vest")

        # Recompute base from the linked txn when present (authoritative; handles
        # stale stored base_amount after a base-currency change).
        if ev.get("txn_id"):
            txn = transactions.find_by_id(ev["txn_id"])
            if txn:
                from backend.core.fx import txn_base_amount

                gross_gain_base = round(txn_base_amount(txn, base=base, rates=rates), 2)

        if tax_event != "deferred":
            total_gain_base += gross_gain_base

        event_rows.append(
            {
                "id": ev.get("id"),
                "grant_id": ev.get("grant_id"),
                "tranche_id": ev.get("tranche_id"),
                "symbol": symbol,
                "grant_date": grant.get("grant_date"),
                "vest_date": ev.get("date"),
                "price_paid_per_unit": price_paid,
                "fmv_per_unit": fmv,
                "fmv_currency": fmv_ccy,
                "units": units,
                "gross_gain_base": gross_gain_base,
                "tax_event": tax_event,
                # Join keys back to the ledger/lot spine — dropping these previously meant an
                # agent reconciling a broker vest confirmation had no way to check whether it
                # was already booked, or to which transaction/lot (root-caused 2026-07-09).
                "txn_id": ev.get("txn_id"),
                "lot_id": ev.get("lot_id"),
            }
        )

    return {
        "year": target_year,
        "years": [int(y) for y in years_with_events],
        "base_currency": base,
        "events": event_rows,
        "total_gain_base": round(total_gain_base, 2),
    }
