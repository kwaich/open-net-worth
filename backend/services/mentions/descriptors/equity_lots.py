"""Descriptors for the investment money-fact types using the GENERIC provider (Phase 2b).

RFC 2026-06-25-lucius-generic-entity-registry §10 Phase 2.

These are further exercises of the generic provider (make_search / make_resolve /
_project) over production stores — no search_override or resolve_override; only
rows + id_field + search_fields + fields + label_field + sublabel_fn.

Types added (orders continue after new_types.py's 16–18):
  income   (order=19) — investment income events from IncomeEventStore
  lot      (order=20) — acquisition lots from LotStore
  grant    (order=21) — equity grants (RSU/LTIP) from GrantStore
  vest     (order=22) — tranche vest events from VestEventStore
  disposal (order=25) — realized sell/transfer-out events from RealizedEventStore
           (25, not 23, because 23/24 are already taken by connector/report — order only
           affects search tie-break ranking, so it appends cleanly after the catalog)

LABEL: each of these four stores keys money facts to an ``instrument_id`` FK and
carries NO human-readable name.  Bare UUID labels are useless in the @-picker, so
each ``rows`` lambda denormalises the instrument ``symbol`` onto every row (one
InstrumentStore read per source call, cached in an id→symbol map).  ``symbol`` is a
genuine display field (allowlisted, ``label_field="symbol"``); ``value`` stays the
stored ``id`` because ``label_field != id_field``.

MONEY CAVEAT (mirrors the ``alert`` precedent in new_types.py): the ``money`` fields
here (gross_amount, unit_cost, fmv_per_unit, …) are NATIVE-CURRENCY stored scalars —
amounts as paid / per-unit costs in their own ``currency``/``cost_currency``/
``fmv_currency`` — NOT base-recomputed portfolio money.  The generic ``_project``
emits them verbatim with no base/FX claim.  The stale write-time ``base_amount``
snapshot (and ``gross_gain_native`` for vests, which the 8B report recomputes) is
DELIBERATELY EXCLUDED from the allowlist per the CLAUDE.md "never trust stored
base_amount" invariant.  FK ids, nested arrays, and idempotency keys are excluded —
not model-display fields.

Dependency rule: lazy in-lambda store imports (services must not couple to db at
module scope).
"""

from __future__ import annotations

from backend.services.mentions._collect import register
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.types import SearchQuery


def _symbol_map() -> dict[str, str]:
    """instrument_id → display symbol, built once per source call.

    Lazy import keeps the dependency rule (no db import at module scope).
    """
    from backend.db.instrument_store import InstrumentStore

    out: dict[str, str] = {}
    for it in InstrumentStore().all():
        iid = it.get("id")
        if iid:
            out[iid] = (it.get("symbol") or "").strip()
    return out


def _with_symbol(rows: list[dict], symbols: dict[str, str]) -> list[dict]:
    """Return rows enriched with a denormalised ``symbol`` from the instrument FK.

    Never mutates the stored dict — copies each row.  Missing instrument → "" (the
    generic loop then falls back to the stored id for the label).
    """
    return [{**r, "symbol": symbols.get(r.get("instrument_id") or "", "")} for r in rows]


# ── income (order=19) ──────────────────────────────────────────────────────────
# Backed by IncomeEventStore (income_events.json, id_field="id").  One record per
# dividend / interest / distribution a holding paid.


def _income_rows(query: SearchQuery) -> list[dict]:
    from backend.db.income_event_store import IncomeEventStore

    return _with_symbol(IncomeEventStore().all(), _symbol_map())


def _income_sublabel(row: dict) -> str:
    kind = row.get("income_kind") or ""
    paid_at = row.get("paid_at") or ""
    return f"{kind} • {paid_at}".strip(" •")


register(
    EntityDescriptor(
        type="income",
        label="Income",
        icon="⥃",
        order=19,
        tone="invest",
        surfaces=frozenset({"entity"}),
        rows=_income_rows,
        id_field="id",
        label_field="symbol",
        search_fields=("symbol", "income_kind", "note"),
        sublabel_fn=_income_sublabel,
        fields={
            "symbol": FieldSpec("Symbol"),
            "income_kind": FieldSpec("Kind", "enum"),
            "gross_amount": FieldSpec("Gross amount", "money"),
            "tax_withheld": FieldSpec("Tax withheld", "money"),
            "currency": FieldSpec("Currency"),
            "quantity": FieldSpec("Quantity", "qty"),
            "amount_per_unit": FieldSpec("Amount per unit", "money"),
            "is_drp": FieldSpec("Reinvested"),
            "paid_at": FieldSpec("Paid", "date"),
            "note": FieldSpec("Note"),
            "created_at": FieldSpec("Created", "date"),
        },
    )
)

# ── lot (order=20) ─────────────────────────────────────────────────────────────
# Backed by LotStore (lots.json, id_field="id").  One dated acquisition tranche
# with its own unit cost; FIFO basis inventory behind a position.


def _lot_rows(query: SearchQuery) -> list[dict]:
    from backend.db.lot_store import LotStore

    return _with_symbol(LotStore().all(), _symbol_map())


def _lot_sublabel(row: dict) -> str:
    kind = row.get("kind") or ""
    remaining = row.get("remaining_qty")
    acquired = row.get("acquired_at") or ""
    qty = f"{remaining} left" if remaining is not None else ""
    return " • ".join(p for p in (kind, qty, acquired) if p)


register(
    EntityDescriptor(
        type="lot",
        label="Lot",
        icon="▭",
        order=20,
        tone="invest",
        surfaces=frozenset({"entity"}),
        rows=_lot_rows,
        id_field="id",
        label_field="symbol",
        search_fields=("symbol", "kind", "note"),
        sublabel_fn=_lot_sublabel,
        fields={
            "symbol": FieldSpec("Symbol"),
            "kind": FieldSpec("Kind", "enum"),
            "acquired_at": FieldSpec("Acquired", "date"),
            "original_qty": FieldSpec("Original qty", "qty"),
            "remaining_qty": FieldSpec("Remaining qty", "qty"),
            "unit_cost": FieldSpec("Unit cost", "money"),
            "cost_currency": FieldSpec("Cost currency"),
            "fees": FieldSpec("Fees", "money"),
            "note": FieldSpec("Note"),
            "created_at": FieldSpec("Created", "date"),
        },
    )
)

# ── grant (order=21) ───────────────────────────────────────────────────────────
# Backed by GrantStore (grants.json, id_field="id").  One equity-comp grant
# (RSU/LTIP) with a flat tranche list (the nested ``tranches`` array is excluded —
# only top-level scalar display fields are allowlisted).


def _grant_rows(query: SearchQuery) -> list[dict]:
    from backend.db.grant_store import GrantStore

    return _with_symbol(GrantStore().all(), _symbol_map())


def _grant_sublabel(row: dict) -> str:
    gtype = (row.get("type") or "").upper()
    status = row.get("status") or ""
    return f"{gtype} • {status}".strip(" •")


register(
    EntityDescriptor(
        type="grant",
        label="Grant",
        icon="❖",
        order=21,
        tone="invest",
        surfaces=frozenset({"entity"}),
        rows=_grant_rows,
        id_field="id",
        label_field="symbol",
        search_fields=("symbol", "type", "note"),
        sublabel_fn=_grant_sublabel,
        fields={
            "symbol": FieldSpec("Symbol"),
            "type": FieldSpec("Type", "enum"),
            "grant_date": FieldSpec("Grant date", "date"),
            "total_units": FieldSpec("Total units", "qty"),
            "currency": FieldSpec("Currency"),
            "tax_event": FieldSpec("Tax event", "enum"),
            "status": FieldSpec("Status", "enum"),
            "note": FieldSpec("Note"),
            "created_at": FieldSpec("Created", "date"),
        },
    )
)

# ── vest (order=22) ────────────────────────────────────────────────────────────
# Backed by VestEventStore (vest_events.json, id_field="id").  One tranche vest
# action.  gross_gain_native is a write-time snapshot the 8B report recomputes —
# EXCLUDED per the "never trust stored snapshot" invariant; base_amount likewise.


def _vest_rows(query: SearchQuery) -> list[dict]:
    from backend.db.vest_event_store import VestEventStore

    rows = VestEventStore().all()
    symbols = _symbol_map()
    grant_iid = _grant_instrument_map()
    # vest_events carry grant_id, not instrument_id — hop grant → instrument → symbol.
    return [
        {**r, "symbol": symbols.get(grant_iid.get(r.get("grant_id") or "", ""), "")} for r in rows
    ]


def _grant_instrument_map() -> dict[str, str]:
    """grant_id → instrument_id, for the vest → grant → instrument → symbol hop."""
    from backend.db.grant_store import GrantStore

    out: dict[str, str] = {}
    for g in GrantStore().all():
        gid = g.get("id")
        if gid:
            out[gid] = g.get("instrument_id") or ""
    return out


def _vest_sublabel(row: dict) -> str:
    units = row.get("units")
    date = row.get("date") or ""
    qty = f"{units} units" if units is not None else ""
    return " • ".join(p for p in (qty, date) if p)


register(
    EntityDescriptor(
        type="vest",
        label="Vest",
        icon="⟠",
        order=22,
        tone="invest",
        surfaces=frozenset({"entity"}),
        rows=_vest_rows,
        id_field="id",
        label_field="symbol",
        search_fields=("symbol", "note"),
        sublabel_fn=_vest_sublabel,
        fields={
            "symbol": FieldSpec("Symbol"),
            "date": FieldSpec("Vest date", "date"),
            "units": FieldSpec("Units", "qty"),
            "fmv_per_unit": FieldSpec("FMV per unit", "money"),
            "fmv_currency": FieldSpec("FMV currency"),
            # In fmv_currency, but not directly paired — typed "text" (not "money") to avoid a
            # currency-less money render; the model still has fmv_currency in the payload.
            "price_paid_per_unit": FieldSpec("Price paid per unit", "text"),
            "units_withheld": FieldSpec("Units withheld", "qty"),
            "note": FieldSpec("Note"),
            "created_at": FieldSpec("Created", "date"),
        },
    )
)

# ── disposal (order=25) ────────────────────────────────────────────────────────
# Backed by RealizedEventStore (realized_events.json, id_field="id").  One realized
# sell / transfer-out — the "disposal" side of the position spine, mirror of "lot".
# proceeds_per_unit / disposal_fees / realized_gain are NATIVE scalars in
# proceeds_currency (emitted verbatim).  The stale base snapshots
# (realized_gain_base / base_currency / fx_rate) are EXCLUDED per the CLAUDE.md
# "never trust stored base_amount" invariant, as are the nested ``consumptions``
# audit array and the FK ids (position_id / instrument_id / account_id / txn_id).


def _disposal_rows(query: SearchQuery) -> list[dict]:
    from backend.db.realized_event_store import RealizedEventStore

    return _with_symbol(RealizedEventStore().all(), _symbol_map())


def _disposal_sublabel(row: dict) -> str:
    qty = row.get("quantity")
    date = row.get("disposed_at") or ""
    units = f"{qty} units" if qty is not None else ""
    return " • ".join(p for p in (units, date) if p)


register(
    EntityDescriptor(
        type="disposal",
        label="Disposal",
        icon="▱",
        order=25,
        tone="neutral",
        surfaces=frozenset({"entity"}),
        rows=_disposal_rows,
        id_field="id",
        label_field="symbol",
        search_fields=("symbol", "method", "note"),
        sublabel_fn=_disposal_sublabel,
        fields={
            "symbol": FieldSpec("Symbol"),
            "disposed_at": FieldSpec("Disposed", "date"),
            "quantity": FieldSpec("Quantity", "qty"),
            "proceeds_per_unit": FieldSpec("Proceeds per unit", "money"),
            "proceeds_currency": FieldSpec("Proceeds currency"),
            "disposal_fees": FieldSpec("Disposal fees", "money"),
            "method": FieldSpec("Method", "enum"),
            "is_taxable": FieldSpec("Taxable"),
            "cost_basis_known": FieldSpec("Cost basis known"),
            # Native realized P&L in proceeds_currency (per-line sum) — NOT the stale
            # realized_gain_base snapshot, which stays excluded from the allowlist.
            "realized_gain": FieldSpec("Realized gain", "money"),
            "note": FieldSpec("Note"),
            "created_at": FieldSpec("Created", "date"),
        },
    )
)
