"""Server-chosen display labels for the opaque ids money-bearing reads return.

WHY THIS EXISTS. Category/payee/account/instrument ids are deliberately opaque
(``cat_<12hex>``, ``acc_<12hex>``, a uuid) — ``label`` is the only human-readable field.
A tool that returns ``category="cat_13095ba70000"`` and nothing else hands the model a
token it cannot interpret, and the model does not reliably stop to resolve it: in the
2026-07-19 incident it answered from ids alone, INVENTED a category name that does not
exist in the book for two rows, and reported three more as having no category at all
when every one of them carried an id. Neither error was hedged. Attaching the label the
server already has removes the join the model kept skipping.

WHY PER-ROW AND NOT A LEGEND. A legend (`{id: label}` beside the rows) is the obvious
alternative and is cheaper only once each id repeats ~3x — it pays the expensive opaque
id TWICE (once as its own key, once in the row) where a per-row label pays it once, and
83.5% of real ``get_transactions`` calls are <=25 rows, where per-row measures cheaper
outright. The deciding argument is not bytes: a legend requires precisely the id->label
join the model demonstrably failed to perform, offered a second time in the same
response. Per-row makes skipping it structurally impossible. ``get_portfolio`` already
ships this shape (``account_name``/``label``/``ticker`` beside the ids), so this is the
existing in-repo convention rather than a new one.

WHAT THIS IS NOT. Every label is ADDITIVE and SERVER-CHOSEN: it selects nothing away and
removes/renames no existing field. This must never grow a caller-chosen
``fields``/``include``/projection parameter — that is the banned design that let a model
select ``amount`` over ``base_amount`` and silently defeat the historical-rate lock
(2026-07-09; ``ai/tools/ledger.py::_project_txn`` is ``return dict(t)`` because of it).
"""

from __future__ import annotations

# Every map is {opaque id: human-readable string}. Built ONCE per response and passed
# down — never a per-row store lookup inside a loop, since these decorate money-bearing
# rows that can run to hundreds of entries.
MapSet = dict[str, dict[str, str]]


def label_maps(
    *,
    categories: bool = False,
    payees: bool = False,
    accounts: bool = False,
    instruments: bool = False,
) -> MapSet:
    """Build only the id->label maps the caller asks for.

    Opt-in per map rather than "always build all four" because the callers genuinely
    differ (a ledger read needs categories/payees/accounts and never instruments; a lot
    read is the reverse) and each map is a full store read.
    """
    maps: MapSet = {}
    if categories:
        from backend.services.categories import get_all_categories

        maps["categories"] = {
            c["id"]: c.get("label", "") for c in get_all_categories(include_hidden=True)
        }
    if payees:
        from backend.services import payee_registry

        maps["payees"] = payee_registry.payee_names()
    if accounts:
        from backend.db.account_store import AccountStore

        maps["accounts"] = {a["id"]: a.get("name", "") for a in AccountStore().all()}
    if instruments:
        from backend.db.instrument_store import InstrumentStore

        rows = InstrumentStore().all()
        maps["instruments"] = {i["id"]: i.get("name", "") for i in rows}
        maps["instrument_tickers"] = {i["id"]: i.get("symbol", "") for i in rows}
    return maps


def _put(out: dict, key: str, source_id, table: dict[str, str] | None) -> None:
    """Attach ``key`` from ``table`` when the id resolves to a non-empty label.

    A missing id or an unresolvable one attaches NOTHING rather than an empty string:
    absent reads as "no label available", where ``""`` reads as "this thing is named
    nothing" — the second is a claim, and a wrong claim about a name is the whole bug.
    """
    if not source_id or not table:
        return
    label = table.get(source_id)
    if label:
        out[key] = label


def label_txn(t: dict, maps: MapSet | None) -> dict:
    """A transaction row with its category/payee/account labels attached beside the ids.

    Additive only — every stored field is returned untouched (this is still the
    "return everything" contract ``_project_txn`` restored in 2026-07-09). Split lines are
    labelled too: a split row's ``category`` is exactly as opaque as the top-level one, and
    a split transaction stores the sentinel ``category="split"`` up top, so without this the
    per-line categories are the ONLY category information in the payload and all of it is
    unresolved.
    """
    out = dict(t)
    if not maps:
        return out
    cats = maps.get("categories")
    _put(out, "category_label", t.get("category"), cats)
    _put(out, "payee_name", t.get("payee_id"), maps.get("payees"))
    accounts = maps.get("accounts")
    _put(out, "from_account_name", t.get("from_account_id"), accounts)
    _put(out, "to_account_name", t.get("to_account_id"), accounts)
    splits = t.get("splits")
    if isinstance(splits, list) and cats:
        # Copy every line before touching it: dict(t) above is SHALLOW, so mutating a
        # split in place would write the label back into the store's own cached row.
        labelled = []
        for line in splits:
            if isinstance(line, dict):
                line_out = dict(line)
                _put(line_out, "category_label", line.get("category"), cats)
                labelled.append(line_out)
            else:
                labelled.append(line)
        out["splits"] = labelled
    return out


def label_instrument_row(row: dict, maps: MapSet | None) -> dict:
    """A row carrying ``instrument_id`` / ``account_id`` with those ids' names attached.

    Shared by the lot and income-event surfaces — both return money-bearing rows whose
    only identification of WHAT was bought or WHO paid is an opaque uuid.
    """
    out = dict(row)
    if not maps:
        return out
    _put(out, "instrument_label", row.get("instrument_id"), maps.get("instruments"))
    _put(out, "instrument_ticker", row.get("instrument_id"), maps.get("instrument_tickers"))
    _put(out, "account_name", row.get("account_id"), maps.get("accounts"))
    return out


def label_txns(rows: list[dict]) -> list[dict]:
    """``label_txn`` over a page of rows with ONE set of maps — the shape every transaction
    read the app's own screens make, so a row arrives with its names instead of making the
    client fetch whole payee/category/account lists to look ids up."""
    maps = label_maps(categories=True, payees=True, accounts=True)
    out = [label_txn(t, maps) for t in rows]
    attach_traded(out)
    return out


def attach_traded(rows: list[dict]) -> None:
    """An investment cash leg names what it bought or sold, and at what price. `ticker` and
    `quantity` are stored on the row by the trade choke (lots._traded); a leg written before
    that, or by hand, gets them here from the lot it opened (a buy) or the realized event it
    settled (a sale), each linked back by `txn_id`. The PRICE lives only on that lot/event —
    `unit_price` is always read from there, never stored twice. A label, never a write. A leg
    two lots share (one order, two fills) sums their quantity and averages their price."""
    pending = {t["id"]: t for t in rows if t.get("kind") == "investment" and t.get("id")}
    if not pending:
        return
    from backend.db.instrument_store import InstrumentStore
    from backend.db.lot_store import LotStore
    from backend.db.realized_event_store import RealizedEventStore

    fills: dict[str, list[tuple[str, float, object, object]]] = {}
    for rec, keys in [
        (r, ("original_qty", "unit_cost", "cost_currency")) for r in LotStore().all()
    ] + [
        (r, ("quantity", "proceeds_per_unit", "proceeds_currency"))
        for r in RealizedEventStore().all()
    ]:
        txn_id = rec.get("txn_id")
        if txn_id not in pending or not rec.get("instrument_id"):
            continue
        qty_key, px_key, ccy_key = keys
        fills.setdefault(txn_id, []).append(
            (
                rec["instrument_id"],
                float(rec.get(qty_key) or 0.0),
                rec.get(px_key),
                rec.get(ccy_key),
            )
        )
    if not fills:
        return
    symbols = {i["id"]: i.get("symbol") or "" for i in InstrumentStore().all()}
    for txn_id, legs in fills.items():
        row = pending[txn_id]
        legs = [f for f in legs if f[0] == legs[0][0]]
        qty = sum(f[1] for f in legs)
        symbol = symbols.get(legs[0][0])
        if symbol and not row.get("ticker"):
            row["ticker"] = symbol
            if qty:
                row["quantity"] = round(qty, 8)
        _put_price(row, legs, qty)


def _put_price(row: dict, legs: list, qty: float) -> None:
    ccys = {str(f[3] or "").upper() for f in legs}
    if not qty or len(ccys) != 1 or "" in ccys or any(not f[2] for f in legs):
        return
    row["unit_price"] = round(sum(f[1] * float(f[2]) for f in legs) / qty, 8)
    row["unit_price_currency"] = ccys.pop()
