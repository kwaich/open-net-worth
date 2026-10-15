"""Write the demo's investment side through the real choke points.

Instruments and price history go in directly (they are reference data, not money facts);
everything that IS a money fact — a lot, a disposal, a dividend, a grant, a vest — goes
through the service choke it belongs to, so the demo book obeys exactly the same
invariants a hand-entered one does. That is the point: a demo built by bypassing the
write path is a demo that can show a state the app cannot actually produce.
"""

from __future__ import annotations

import logging
from datetime import date

from backend.scripts.demo import markets
from backend.scripts.demo._timeline import Timeline, add_months

log = logging.getLogger(__name__)


def create_instruments(tl: Timeline) -> dict[str, str]:
    """Create every instrument row. Returns symbol → instrument id.

    Deliberately does NOT record price history: the history has to be anchored on today's live
    price, and ``markets.live_anchors`` can only route a CoinGecko coin correctly once the
    instrument rows it reads exist (``prices._crypto_routing_with_currencies`` resolves the
    source off the store — with an empty store, "bitcoin" is sent to Yahoo as ``$BITCOIN`` and
    fails). Rows first, then anchors, then ``record_history``.
    """
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    out: dict[str, str] = {}
    for spec in markets.INSTRUMENTS:
        data = {
            "symbol": spec["symbol"],
            "name": spec["name"],
            "asset_class": spec["asset_class"],
            "asset_subclass": spec["asset_subclass"],
            "sector": spec["sector"],
            "quote_currency": "USD",
            "price_source": spec["price_source"],
            "price_source_id": spec["price_source_id"],
            "precision": spec.get("precision", 4),
        }
        if spec.get("dividend_rate_annual"):
            data["dividend_rate_annual"] = spec["dividend_rate_annual"]
            data.update(_dividend_calendar(tl, spec))
        if spec.get("manual"):
            data["manual_price"] = spec["anchor"]
        out[spec["symbol"]] = store.create(data)["id"]
    return out


def record_history(series: dict[str, list[dict]]) -> int:
    """Write the generated daily closes. Returns the number of points recorded."""
    from backend.db.price_history_store import PriceHistoryStore

    prices = PriceHistoryStore()
    recorded = 0
    for spec in markets.INSTRUMENTS:
        pid = spec["price_source_id"]
        if pid:
            recorded += prices.record_backfill_series(
                pid, series[spec["symbol"]], source="backfill"
            )
    return recorded


def _dividend_calendar(tl: Timeline, spec: dict) -> dict:
    """The declared/last dividend dates a payer carries.

    Without these the dividend calendar and the income forecast have nothing to project from
    and render empty, even though the book is full of paid dividends — the calendar reads the
    INSTRUMENT's schedule, not the history. Anchored on the same quarterly cycle
    ``book_dividends`` pays on, so the two never contradict each other.
    """
    last_quarter = tl.today.replace(day=24)
    while last_quarter.month % 3 or last_quarter > tl.today:
        last_quarter = add_months(last_quarter, -1)
    upcoming = add_months(last_quarter, 3)
    return {
        "dividend_frequency": spec.get("dividend_frequency", "quarterly"),
        "last_ex_date": last_quarter.isoformat(),
        "last_dividend_amount": spec.get("quarterly_dividend"),
        "next_ex_date": upcoming.isoformat(),
        "next_pay_date": add_months(last_quarter, 3).replace(day=28).isoformat(),
    }


def create_positions(orders: list[dict], accounts: dict, instruments: dict) -> dict:
    """One position per (account, symbol) actually traded. Returns (account, symbol) → id."""
    from backend.services import positions as positions_service

    out: dict[tuple[str, str], str] = {}
    for order in orders:
        key = (order["account"], order["symbol"])
        if key in out:
            continue
        # An empty container: every unit arrives as a dated lot in book_lots, so the position
        # itself carries no acquisition date (a dated add must have quantity > 0).
        result = positions_service.apply_position_change(
            {
                "type": "add",
                "instrument_id": instruments[order["symbol"]],
                "account_id": accounts[order["account"]],
                "quantity": 0,
                "cost_currency": "USD",
            }
        )
        if result.get("error"):
            raise RuntimeError(f"position add failed for {key}: {result['error']}")
        out[key] = result["position_id"]
    return out


def book_lots(orders: list[dict], positions: dict) -> int:
    """Every purchase, as a lot on its position. The cash legs are written by the ledger
    pass — a lot and the transaction that paid for it are two facts about one event."""
    from backend.services import lots as lots_service

    booked = 0
    for order in orders:
        result = lots_service.apply_lot_change(
            {
                "type": "acquire",
                "position_id": positions[(order["account"], order["symbol"])],
                "quantity": order["quantity"],
                "unit_cost": order["unit_cost"],
                "cost_currency": "USD",
                "acquired_at": order["date"].isoformat(),
                "fees": order["fees"],
                "kind": "buy",
                "allow_duplicate": True,
                "idempotency_key": f"demo:buy:{order['account']}:{order['symbol']}:{order['date']}",
            }
        )
        if result.get("error"):
            raise RuntimeError(f"lot acquire failed: {result['error']}")
        booked += 1
    return booked


def book_trims(trims: list[dict], positions: dict) -> int:
    """The two sells, through the disposal choke — FIFO lot consumption and a realized event.

    ``proceeds_account_id`` is deliberately NOT passed: that path books the cash through the
    choke's own synthetic "Market (USD)" counterparty account, which would put a confusing
    negative-balance account on the demo's Accounts screen. The demo settles the proceeds as
    an ordinary ``investment`` row instead (``build._investment_cash_legs``) — same cash, same
    date, symmetrical with how every buy in this book settles.
    """
    from backend.services import lots as lots_service

    done = 0
    for trim in trims:
        result = lots_service.apply_lot_change(
            {
                "type": "dispose",
                "position_id": positions[(trim["account"], trim["symbol"])],
                "quantity": trim["quantity"],
                "proceeds_per_unit": trim["unit_price"],
                "proceeds_currency": "USD",
                "disposed_at": trim["date"].isoformat(),
                "disposal_fees": trim["fees"],
                "method": "fifo",
                "note": "Rebalancing trim.",
                "idempotency_key": f"demo:sell:{trim['account']}:{trim['symbol']}:{trim['date']}",
            }
        )
        if result.get("error"):
            log.warning(
                "demo trim skipped (%s %s): %s", trim["account"], trim["symbol"], result["error"]
            )
            continue
        done += 1
    return done


# ── Dividends ─────────────────────────────────────────────────────────────────
# Per-unit quarterly rates for the payers, in USD. Paid into the account that holds them.
DIVIDEND_RATES = {"SCHD": 0.26, "BND": 0.24, "AAPL": 0.26}


def book_dividends(tl: Timeline, positions: dict, orders: list[dict]) -> int:
    """Quarterly cash dividends, sized off the units actually held on the pay date."""
    from backend.services import income as income_service

    booked = 0
    for (account, symbol), position_id in sorted(positions.items()):
        rate = DIVIDEND_RATES.get(symbol)
        if rate is None:
            continue
        for m in tl.months:
            if m.month % 3 != 0:
                continue
            paid = tl.clamp(m.replace(day=min(24, 28)))
            if paid is None:
                continue
            units = sum(
                o["quantity"]
                for o in orders
                if o["account"] == account and o["symbol"] == symbol and o["date"] <= paid
            )
            if units <= 0:
                continue
            gross = round(units * rate, 2)
            if gross < 0.5:
                continue
            # A taxable account withholds nothing here, but a real book shows the field.
            tax = round(gross * 0.0, 2)
            result = income_service.apply_income_change(
                {
                    "type": "record",
                    "position_id": position_id,
                    "paid_at": paid.isoformat(),
                    "income_kind": "dividend",
                    "quantity": round(units, 4),
                    "amount_per_unit": rate,
                    "gross_amount": gross,
                    "tax_withheld": tax,
                    "currency": "USD",
                    "idempotency_key": f"demo:div:{position_id}:{paid}",
                }
            )
            if not result.get("error"):
                booked += 1
    return booked


# ── The RSU grant ─────────────────────────────────────────────────────────────


def categorize_investment_income(categories: dict) -> int:
    """Classify the income rows the investment chokes write with no category.

    ``income.apply_income_change`` books its cash leg with ``category=None`` on purpose —
    the write path records facts, and the tax verdict is derived later, not frozen at write.
    That is right for the app and wrong for a demo: it leaves an "Uncategorized" pile on the
    transactions screen. Classifying them here is exactly the follow-up action the owner
    would take, and it uses the ordinary update path. (A vest no longer writes any cash leg
    to classify — ADR 2026-09-22-a-vest-books-no-income-transaction.)
    """
    from backend.services import ledger as ledger_service

    stamped = 0
    for txn in ledger_service.transactions.all():
        if txn.get("category") or txn.get("kind") != "income":
            continue
        tags = txn.get("tags") or []
        if "dividend" in tags:
            category = categories["investment_income"]
        elif "vest" in tags:
            category = categories["equity_comp"]
        else:
            continue
        ledger_service.update_transaction(txn["id"], {"category": category})
        stamped += 1
    return stamped


def book_grant(tl: Timeline, accounts: dict, instruments: dict, series: dict) -> dict:
    """Create the grant, then vest every tranche whose date has already passed.

    The unvested remainder is what makes the demo show ``unvested_base`` as its own figure
    — excluded from net worth, reported beside it, with a dated next tranche.
    """
    from backend.services import equity as equity_service

    schedule = markets.grant_schedule(tl, series["NET"])
    created = equity_service.apply_equity_change(
        {
            "type": "grant_create",
            "grant_type": "rsu",
            "instrument_id": instruments["NET"],
            "account_id": accounts["brokerage"],
            "grant_date": schedule["grant_date"],
            "total_units": schedule["total_units"],
            "currency": "USD",
            "tax_event": "at_vest",
            "tranches": schedule["tranches"],
            "note": "New-hire RSU grant, four-year quarterly vest with a one-year cliff.",
            "idempotency_key": "demo:grant:net",
        }
    )
    if created.get("error"):
        raise RuntimeError(f"grant create failed: {created['error']}")
    grant_id = created["grant_id"]

    from backend.db.grant_store import GrantStore

    grant = GrantStore().find_by_id(grant_id)
    vested = 0
    for tranche in grant.get("tranches") or []:
        vest_date = date.fromisoformat(tranche["vest_date"])
        if vest_date > tl.today:
            continue
        fmv = markets.price_on(series["NET"], vest_date)
        result = equity_service.apply_equity_change(
            {
                "type": "vest_apply",
                "grant_id": grant_id,
                "tranche_id": tranche["id"],
                "units": tranche["units"],
                "fmv_per_unit": fmv,
                "fmv_currency": "USD",
                "vest_date": tranche["vest_date"],
            }
        )
        if result.get("error"):
            raise RuntimeError(f"vest failed for {tranche['vest_date']}: {result['error']}")
        vested += 1
    return {"grant_id": grant_id, "vested_tranches": vested}


# ``_settle_vest`` lived here until 2026-09-22. It booked an offsetting ``investment``
# debit for the vest's full FMV, because the vest choke credited the brokerage with that
# amount AND minted the share lot — "leaving it there shows the same value twice: once as
# settlement cash, once as shares" (its own docstring). That was the real double-count
# being papered over in demo DATA instead of fixed at the choke; a vest now books no cash
# leg at all (ADR 2026-09-22-a-vest-books-no-income-transaction), so the offset has nothing
# to offset and would overdraw the brokerage by the vest's value.
