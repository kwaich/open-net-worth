"""
activity_service — unified investment-activity aggregation.

A pure read-only aggregator: merges lot acquires (buys), realized disposals (sells, or a
transfer when the disposal is non-taxable), dividend/coupon/interest income events,
vest events, and trading fees into a typed, signed, date-DESC feed.

Output shape matches the frontend ActivityFeed contract exactly:
  { base, events: [ActivityRow], summary: { count_30d, net_invested_30d, income_30d, fees_30d } }

Each ActivityRow is FLAT:
  { source_id, source_kind, date, type, symbol, name, detail,
    account_id, account_name, amount_base, signed, currency, instrument_id, links }

amount_base follows the historical-rate lock (ADR 2026-07-15, mirrors
lots.realized_pnl / core.fx.txn_base_amount): a row's write-time fx_rate/base_currency
stamp is TRUSTED while it still matches the current base — a completed buy/sell/vest is
a historical fact, not revalued as FX drifts. Only a base-currency change or an
unstamped/legacy row recomputes via core.fx at current rates.
No writes, no choke point, no new store. Imports db + core.fx.
"""

from __future__ import annotations

from datetime import timedelta

from backend.core.fx import base_currency, to_base, txn_base_amount
from backend.core.user_time import user_today
from backend.db.account_store import AccountStore
from backend.db.grant_store import GrantStore
from backend.db.income_event_store import IncomeEventStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.transaction_store import TransactionStore
from backend.db.vest_event_store import VestEventStore

_accounts = AccountStore()
_grants = GrantStore()
_income_events = IncomeEventStore()
_instruments = InstrumentStore()
_lots = LotStore()
_realized_events = RealizedEventStore()
_transactions = TransactionStore()
_vest_events = VestEventStore()


def _load_rates() -> dict:
    try:
        from backend.services.fx_cache import load_fx_cache

        return load_fx_cache()
    except Exception:
        return {}


def _acct_index() -> dict[str, dict]:
    return {a["id"]: a for a in _accounts.query_accounts(include_archived=True)}


def _inst_index() -> dict[str, dict]:
    return {i["id"]: i for i in _instruments.all()}


def _inst_info(instrument_id: str | None, inst_idx: dict) -> tuple[str | None, str | None]:
    if not instrument_id:
        return None, None
    inst = inst_idx.get(instrument_id) or {}
    symbol = inst.get("symbol") or None
    name = inst.get("name") or symbol or None
    return symbol, name


def _acct_info(account_id: str | None, acct_idx: dict) -> tuple[str, str]:
    if not account_id:
        return "", ""
    a = acct_idx.get(account_id) or {}
    return account_id, a.get("name") or ""


# ── Per-source row builders ───────────────────────────────────────────────────


# Lot kinds that are NOT a purchase: no cash left the account when they were minted.
# A ``grant`` lot is the shares an RSU release delivered — the vest_event row already
# tells that story, so reporting the lot too showed every vest twice, the second time as
# a "Buy −FMV×units" that also inflated net-invested (owner, 2026-09-04: KSTL release at
# a workplace custodian appearing as a −S$27k purchase). ``transfer_in`` is shares moved
# between custodians; the original buy is the purchase.
_NON_PURCHASE_LOT_KINDS = frozenset({"grant", "transfer_in"})


def _locked_base_amount(
    raw_native: float,
    native_ccy: str,
    fx_rate: float | None,
    stamped_base: str | None,
    rates: dict,
    base: str,
) -> float:
    """Historical-rate lock (ADR 2026-07-15, mirrors ``lots.realized_pnl`` /
    ``core.fx.txn_base_amount``): trust a lot's/event's write-time ``fx_rate`` while its
    ``base_currency`` stamp still matches the current base — a completed buy or sell is a
    historical fact, not revalued as FX drifts. Otherwise recompute via core.fx at
    current rates (unstamped/legacy row, or the base currency changed since the write)."""
    if fx_rate and (stamped_base or "").upper() == base:
        return raw_native * fx_rate
    return to_base(raw_native, native_ccy, base, rates=rates)


def _vest_locked_base_amount(
    txn_id: str | None, gross_native: float, fmv_ccy: str, rates: dict, base: str
) -> float:
    """Historical-rate lock for a vest (mirrors ``equity_report.equity_report``): a
    vest's linked transaction carries the authoritative write-time stamp, so trust
    ``core.fx.txn_base_amount`` on it rather than revaluing the FMV gain at current
    rates. Falls back to a current-rate recompute when there's no linked transaction
    (or it was deleted) — the vest event itself carries no fx_rate/base_currency stamp."""
    if txn_id:
        txn = _transactions.find_by_id(txn_id)
        if txn:
            return txn_base_amount(txn, base=base, rates=rates)
    return to_base(gross_native, fmv_ccy, base, rates=rates)


def _buy_rows(
    lots: list[dict],
    inst_idx: dict,
    acct_idx: dict,
    rates: dict,
    base: str,
    account_id: str | None,
    date_from: str | None,
    date_to: str | None,
) -> list[dict]:
    rows: list[dict] = []
    for lot in lots:
        if lot.get("kind") in _NON_PURCHASE_LOT_KINDS:
            continue
        acquired_at = lot.get("acquired_at") or ""
        if date_from and acquired_at < date_from:
            continue
        if date_to and acquired_at > date_to:
            continue
        if account_id and lot.get("account_id") != account_id:
            continue
        qty = float(lot.get("original_qty") or 0)
        unit_cost = lot.get("unit_cost")
        cost_ccy = (lot.get("cost_currency") or base).upper()
        symbol, name = _inst_info(lot.get("instrument_id"), inst_idx)
        acct_id, acct_name = _acct_info(lot.get("account_id"), acct_idx)

        raw_cost = float(unit_cost) * qty if unit_cost is not None else 0.0
        amount_base = round(
            -_locked_base_amount(
                raw_cost, cost_ccy, lot.get("fx_rate"), lot.get("base_currency"), rates, base
            ),
            2,
        )

        detail = f"{qty:g} @ {float(unit_cost):g} {cost_ccy}" if unit_cost is not None else None

        rows.append(
            {
                "source_id": f"buy:{lot['id']}",
                "source_kind": "lot",
                "date": acquired_at,
                "type": "buy",
                "symbol": symbol,
                "name": name,
                "detail": detail,
                "account_id": acct_id,
                "account_name": acct_name,
                "amount_base": amount_base,
                "signed": True,
                "currency": cost_ccy,
                "instrument_id": lot.get("instrument_id") or None,
                "links": {
                    "lot_id": lot.get("id"),
                    "position_id": lot.get("position_id"),
                    "txn_id": lot.get("txn_id") or None,
                },
            }
        )

        fee = float(lot.get("fees") or 0)
        if fee > 0:
            fee_base = round(
                -_locked_base_amount(
                    fee, cost_ccy, lot.get("fx_rate"), lot.get("base_currency"), rates, base
                ),
                2,
            )
            rows.append(
                {
                    "source_id": f"fee:{lot['id']}:acq",
                    "source_kind": "lot_fee",
                    "date": acquired_at,
                    "type": "fee",
                    "symbol": symbol,
                    "name": name,
                    "detail": f"acquisition fee {fee:g} {cost_ccy}",
                    "account_id": acct_id,
                    "account_name": acct_name,
                    "amount_base": fee_base,
                    "signed": True,
                    "currency": cost_ccy,
                    "instrument_id": lot.get("instrument_id") or None,
                    "links": {"lot_id": lot.get("id")},
                }
            )
    return rows


def _is_movement(ev: dict) -> bool:
    """A NON-taxable disposal is the model's share movement — a transfer to another of the
    user's accounts, a gift, or a write-off (DisposalCreate: is_taxable=False carries the
    basis out and books no gain; the choke refuses a taxable $0 sale precisely so this is
    the only $0 path). No cash arrived, so it is not a sale — reporting it as "Sell 1242 @
    0 EUR +S$0.00" was the owner's 2026-09-04 report (a workplace-custodian → IBKR move).
    ONE informational row, like a vest: direction is not a type, the destination is detail."""
    is_taxable = ev.get("is_taxable")
    return is_taxable is not None and not is_taxable


def _transfer_dest(ev: dict) -> str | None:
    return ev.get("transfer_to_account_id") or None


def _movement_detail(ev: dict, qty: float, acct_idx: dict) -> str:
    dest = _transfer_dest(ev)
    if not dest:
        return f"{qty:g} units transferred out"  # a gift / write-off / pre-feature move
    _, dest_name = _acct_info(dest, acct_idx)
    return f"{qty:g} units → {dest_name or dest}"


def _sell_rows(
    events: list[dict],
    inst_idx: dict,
    acct_idx: dict,
    rates: dict,
    base: str,
    account_id: str | None,
) -> list[dict]:
    rows: list[dict] = []
    for ev in events:
        # A transfer touches two accounts — it shows under either side's filter.
        if account_id and account_id not in (ev.get("account_id"), _transfer_dest(ev)):
            continue
        qty = float(ev.get("quantity") or 0)
        proceeds_per_unit = float(ev.get("proceeds_per_unit") or 0)
        disposal_fees = float(ev.get("disposal_fees") or 0)
        proceeds_ccy = (ev.get("proceeds_currency") or base).upper()
        disposed_at = ev.get("disposed_at") or ""
        symbol, name = _inst_info(ev.get("instrument_id"), inst_idx)
        acct_id, acct_name = _acct_info(ev.get("account_id"), acct_idx)
        links: dict = {
            "event_id": ev.get("id"),
            "position_id": ev.get("position_id"),
            "txn_id": ev.get("txn_id") or None,
        }

        if _is_movement(ev):
            rtype, amount_base, signed = "transfer", 0.0, False
            detail = _movement_detail(ev, qty, acct_idx)
            links["to_account_id"] = _transfer_dest(ev)
        else:
            raw_proceeds = proceeds_per_unit * qty
            amount_base = round(
                _locked_base_amount(
                    raw_proceeds,
                    proceeds_ccy,
                    ev.get("fx_rate"),
                    ev.get("base_currency"),
                    rates,
                    base,
                ),
                2,
            )
            rtype, detail, signed = "sell", f"{qty:g} @ {proceeds_per_unit:g} {proceeds_ccy}", True

        rows.append(
            {
                "source_id": f"{rtype}:{ev['id']}",
                "source_kind": "realized_event",
                "date": disposed_at,
                "type": rtype,
                "symbol": symbol,
                "name": name,
                "detail": detail,
                "account_id": acct_id,
                "account_name": acct_name,
                "amount_base": amount_base,
                "signed": signed,
                "currency": proceeds_ccy,
                "instrument_id": ev.get("instrument_id") or None,
                "links": links,
            }
        )

        if disposal_fees > 0:
            fee_base = round(
                -_locked_base_amount(
                    disposal_fees,
                    proceeds_ccy,
                    ev.get("fx_rate"),
                    ev.get("base_currency"),
                    rates,
                    base,
                ),
                2,
            )
            rows.append(
                {
                    "source_id": f"fee:{ev['id']}:disp",
                    "source_kind": "realized_event_fee",
                    "date": disposed_at,
                    "type": "fee",
                    "symbol": symbol,
                    "name": name,
                    "detail": f"disposal fee {disposal_fees:g} {proceeds_ccy}",
                    "account_id": acct_id,
                    "account_name": acct_name,
                    "amount_base": fee_base,
                    "signed": True,
                    "currency": proceeds_ccy,
                    "instrument_id": ev.get("instrument_id") or None,
                    "links": {"event_id": ev.get("id")},
                }
            )
    return rows


def _income_rows(
    events: list[dict],
    inst_idx: dict,
    acct_idx: dict,
    rates: dict,
    base: str,
    account_id: str | None,
) -> list[dict]:
    from backend.services.income import event_base_amount

    rows: list[dict] = []
    for ev in events:
        if account_id and ev.get("account_id") != account_id:
            continue
        amount_base = round(event_base_amount(ev, rates=rates, base=base), 2)
        income_kind = ev.get("income_kind") or "dividend"
        symbol, name = _inst_info(ev.get("instrument_id"), inst_idx)
        acct_id, acct_name = _acct_info(ev.get("account_id"), acct_idx)

        qty = ev.get("quantity")
        apu = ev.get("amount_per_unit")
        ccy = (ev.get("currency") or base).upper()
        detail: str | None = None
        if qty is not None and apu is not None:
            detail = f"{float(qty):g} × {float(apu):g} {ccy}"

        rows.append(
            {
                "source_id": f"{income_kind}:{ev['id']}",
                "source_kind": "income_event",
                "date": ev.get("paid_at") or "",
                "type": income_kind,
                "symbol": symbol,
                "name": name,
                "detail": detail,
                "account_id": acct_id,
                "account_name": acct_name,
                "amount_base": amount_base,
                "signed": True,
                "currency": ccy,
                "instrument_id": ev.get("instrument_id") or None,
                "links": {
                    "event_id": ev.get("id"),
                    "position_id": ev.get("position_id"),
                    "txn_id": ev.get("txn_id") or None,
                },
            }
        )
    return rows


def _vest_origin(ev: dict, grant_idx: dict, lot_idx: dict) -> tuple[str | None, str | None]:
    """(instrument_id, account_id) of a vest — NEITHER lives on the event itself.

    A VestEvent is an annotation on its grant: the grant owns the instrument, and the
    lot the vest struck owns the account the shares actually landed in (a grant can be
    moved between custodians, so the lot beats the grant's own account_id). Reading
    ``ev["instrument_id"]`` rendered every vest anonymous and hid them all from the
    account filter (owner, 2026-09-04)."""
    grant = grant_idx.get(ev.get("grant_id") or "") or {}
    lot = lot_idx.get(ev.get("lot_id") or "") or {}
    instrument_id = grant.get("instrument_id") or lot.get("instrument_id") or None
    acct_id = lot.get("account_id") or grant.get("account_id") or None
    return instrument_id, acct_id


def _vest_rows(
    events: list[dict],
    inst_idx: dict,
    acct_idx: dict,
    rates: dict,
    base: str,
    account_id: str | None,
    date_from: str | None,
    date_to: str | None,
    grant_idx: dict | None = None,
    lot_idx: dict | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for ev in events:
        vest_date = ev.get("date") or ""
        if date_from and vest_date < date_from:
            continue
        if date_to and vest_date > date_to:
            continue
        ev_inst_id, linked_acct_id = _vest_origin(ev, grant_idx or {}, lot_idx or {})
        if account_id and linked_acct_id != account_id:
            continue

        gross_native = float(ev.get("gross_gain_native") or 0)
        fmv_ccy = (ev.get("fmv_currency") or base).upper()
        is_deferred = ev.get("txn_id") is None and gross_native > 0
        amount_base = (
            0.0
            if is_deferred
            else round(
                _vest_locked_base_amount(ev.get("txn_id"), gross_native, fmv_ccy, rates, base), 2
            )
        )

        symbol, name = _inst_info(ev_inst_id, inst_idx)
        acct_id, acct_name = _acct_info(linked_acct_id, acct_idx)

        units = ev.get("units")
        fmv = ev.get("fmv_per_unit")
        detail: str | None = None
        if units is not None and fmv is not None:
            detail = f"{float(units):g} units @ FMV {float(fmv):g} {fmv_ccy}"

        rows.append(
            {
                "source_id": f"vest:{ev['id']}",
                "source_kind": "vest_event",
                "date": vest_date,
                "type": "vest",
                "symbol": symbol,
                "name": name,
                "detail": detail,
                "account_id": acct_id,
                "account_name": acct_name,
                "amount_base": amount_base,
                "signed": False,
                "currency": fmv_ccy,
                "instrument_id": ev_inst_id,
                "links": {
                    "event_id": ev.get("id"),
                    "grant_id": ev.get("grant_id"),
                    "txn_id": ev.get("txn_id") or None,
                    "lot_id": ev.get("lot_id") or None,
                },
            }
        )
    return rows


# ── Summary computation ───────────────────────────────────────────────────────


def _summary_30d(all_events: list[dict]) -> dict:
    cutoff = (user_today() - timedelta(days=30)).isoformat()
    count = 0
    net_invested = 0.0
    income = 0.0
    fees = 0.0
    for row in all_events:
        if (row.get("date") or "") < cutoff:
            continue
        count += 1
        ba = float(row.get("amount_base") or 0)
        rtype = row.get("type") or ""
        if rtype in ("buy", "sell"):
            # Rows are CASH-FLOW signed (buy = −cost, sell = +proceeds). "Net invested"
            # is the label's semantics: money moved INTO investments minus money taken
            # out = Σ|buys| − Σ|sells| = −Σ(cash flow). The old code added the buy's
            # negative cash flow AND subtracted the sell's positive one, so both flows
            # pushed the figure negative — it displayed −(total volume), not net
            # invested (sign bug, owner report 2026-07-12).
            net_invested -= ba
        elif rtype in ("dividend", "coupon", "interest"):
            # A vest delivers shares, not cash — its FMV is NOT income received (ACT-7;
            # the card read +S$16k the week an RSU tranche landed, owner 2026-09-04).
            income += ba
        elif rtype == "fee":
            fees += abs(ba)
    return {
        "count_30d": count,
        "net_invested_30d": round(net_invested, 2),
        "income_30d": round(income, 2),
        "fees_30d": round(fees, 2),
    }


# ── Public API ────────────────────────────────────────────────────────────────

_VALID_TYPES = {
    "buy",
    "sell",
    "transfer",
    "dividend",
    "coupon",
    "interest",
    "deposit",
    "withdrawal",
    "vest",
    "fee",
}


def activity_feed(
    *,
    type_filter: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    account_id: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    base: str | None = None,
) -> dict:
    """Merged, signed, date-DESC activity feed across investment event sources.

    Returns { base, events: [ActivityRow], summary: { count_30d, ... } }.
    Each row's amount_base follows the historical-rate lock (module docstring) — the
    write-time stamp is trusted while the base currency hasn't changed.
    """
    base = (base or base_currency()).upper()
    rates = _load_rates()

    acct_idx = _acct_index()
    inst_idx = _inst_index()

    all_income = _income_events.query_events(
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
    )
    # Account scoping happens in _sell_rows, not the store: a transfer belongs to BOTH its
    # source and its destination, and only the row builder knows the second one.
    realized = _realized_events.query_events(date_from=date_from, date_to=date_to)
    vest_evs = _vest_events.query_events(date_from=date_from, date_to=date_to)
    lots = _lots.all()

    events: list[dict] = []
    events.extend(_buy_rows(lots, inst_idx, acct_idx, rates, base, account_id, date_from, date_to))
    events.extend(_sell_rows(realized, inst_idx, acct_idx, rates, base, account_id))
    events.extend(_income_rows(all_income, inst_idx, acct_idx, rates, base, account_id))
    if vest_evs:
        grant_idx = {g["id"]: g for g in _grants.all()}
        lot_idx = {lot["id"]: lot for lot in lots}
        events.extend(
            _vest_rows(
                vest_evs,
                inst_idx,
                acct_idx,
                rates,
                base,
                account_id,
                date_from,
                date_to,
                grant_idx=grant_idx,
                lot_idx=lot_idx,
            )
        )

    if type_filter:
        events = [e for e in events if e.get("type") in type_filter]

    events.sort(key=lambda r: (r.get("date") or "", r.get("source_id") or ""), reverse=True)

    summary = _summary_30d(events)

    off = offset or 0
    page = events[off : off + limit] if limit else events[off:]

    return {
        "base": base,
        "events": page,
        "summary": summary,
    }
