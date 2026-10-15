"""Invariant pins for the mention-descriptor read paths (money-correctness guards).

These tests were written to falsify three CLAIMED read-path bugs in
``backend/services/mentions/descriptors/`` (analysis 2026-07-18):

  1. ``_lot_rows``/``_disposal_rows`` use ``Store().all()`` (file order) rather than
     the FIFO reader, allegedly producing wrong realized gains and wrong long/short
     term classification.
  2. ``_transaction_rows`` reads the raw stored ``base_amount`` while
     ``_resolve_transaction`` applies ``txn_base_amount``'s historical-rate trust
     rule, allegedly reporting two different numbers for one transaction.
  3. No cross-entity service reader exists to route either through.

ALL THREE WERE FALSIFIED — see the module-level findings below. Every test here
PASSED at HEAD before any change; none of them is a bug fix. They are kept as
regression pins because the invariants they lock are real and were previously
untested: nothing else in the suite covers LotStore file-order-vs-FIFO
independence, or pins the transaction type's service-backed dispatch.

Findings (why there is no bug):
  - Mention descriptors NEVER consume lots or compute a gain/term. The FIFO queue is
    owned by ``LotStore.open_lots_for_position`` (``_fifo_sorted``), which is what the
    disposal path reads; ``.all()`` only feeds the @-picker's display projection.
  - The ``transaction`` descriptor sets BOTH ``search_override`` and
    ``resolve_override``, so ``_transaction_rows``/``_transaction_find_one`` are
    unreachable; the live resolve path goes through ``txn_base_amount``.
  - The transaction ``fields`` allowlist is keyed to the SERVICE output name
    (``amount_base``), not the stored name (``base_amount``), so even the unreachable
    generic path could not surface a stale stored base amount.
"""

from __future__ import annotations

import pytest

from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.services import lots as lots_svc
from backend.services.mentions import providers
from backend.services.mentions import resolve as mention_resolve
from backend.services.mentions.registry import REGISTRY
from backend.services.mentions.types import SearchQuery

_POSITION = {
    "id": "pos-1",
    "instrument_id": "inst-1",
    "account_id": "acc-1",
    "cost_currency": "USD",
}


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(lots_svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_svc, "base_currency", lambda: "USD")
    return tmp_path


def _q(q: str = "", limit: int = 25) -> SearchQuery:
    return SearchQuery(q=q, types=None, surfaces=frozenset({"entity"}), limit=limit, cursor=None)


def _lot(lot_id: str, acquired_at: str, unit_cost: float, **extra) -> dict:
    return {
        "id": lot_id,
        "position_id": "pos-1",
        "instrument_id": "inst-1",
        "account_id": "acc-1",
        "acquired_at": acquired_at,
        "original_qty": 10,
        "remaining_qty": 10,
        "unit_cost": unit_cost,
        "cost_currency": "USD",
        "kind": "buy",
        **extra,
    }


def _seed_out_of_order_lots() -> None:
    """Write lots in DELIBERATELY anti-FIFO file order: newest first, oldest second.

    The two lots are chosen so consuming the wrong one is unmistakable in BOTH
    dimensions the claim named:
      lot-old (2020-01-01 @ $100) -> long-term,  gain 10x(130-100) = +$300
      lot-new (2026-03-01 @ $120) -> short-term, gain 10x(130-120) = +$100
    """
    store = LotStore()
    store.create(_lot("lot-new", "2026-03-01", 120.0))
    store.create(_lot("lot-old", "2020-01-01", 100.0))


# ── Bug-1 claim: FIFO ordering ────────────────────────────────────────────────


def test_store_file_order_is_not_fifo_order(isolated):
    """Precondition for the whole claim: file order and FIFO order really do differ."""
    _seed_out_of_order_lots()
    store = LotStore()
    assert [r["id"] for r in store.all()] == ["lot-new", "lot-old"]
    assert [r["id"] for r in store.lots_for_position("pos-1")] == ["lot-old", "lot-new"]


def test_disposal_consumes_fifo_despite_anti_fifo_file_order(isolated):
    """The money assertion: a sale consumes the OLDEST lot, booking the long-term
    +$300 gain — never the file-first newest lot's short-term +$100."""
    _seed_out_of_order_lots()
    result = lots_svc.compute_disposal(
        _POSITION,
        LotStore().open_lots_for_position("pos-1"),
        {
            "quantity": 10,
            "proceeds_per_unit": 130.0,
            "proceeds_currency": "USD",
            "disposed_at": "2026-06-01",
            "method": "fifo",
        },
        {"USD": 1.0},
        "USD",
    )
    consumed = result["consumptions"]
    assert [c["lot_id"] for c in consumed] == ["lot-old"], "FIFO must consume the oldest lot"
    assert consumed[0]["term"] == "long", "held since 2020 — long-term, not short"
    assert result["realized_gain"] == pytest.approx(300.0)


def test_lot_and_disposal_descriptors_emit_no_gain_or_term(isolated):
    """Why descriptor row ORDER cannot corrupt tax math: the mention projection is
    display-only. It computes no realized gain and no term, so there is no derived
    figure for an ordering difference to get wrong."""
    InstrumentStore().create(
        {
            "id": "inst-1",
            "symbol": "AAPL",
            "price_source_id": "AAPL",
            "quote_currency": "USD",
            "asset_class": "equity",
        }
    )
    _seed_out_of_order_lots()
    projected = REGISTRY["lot"].resolve("lot-old")
    assert projected is not None
    assert "realized_gain" not in projected
    assert "term" not in projected and "term_to_date" not in projected
    # It emits the stored acquisition facts, each anchored to its own currency.
    assert projected["unit_cost"] == pytest.approx(100.0)
    assert projected["cost_currency"] == "USD"


# ── Bug-2 claim: two base amounts for one transaction ─────────────────────────


def test_transaction_type_dispatches_to_service_backed_overrides():
    """The transaction type must resolve through ``_resolve_transaction`` (which
    applies ``txn_base_amount``'s trust rule), never through the generic raw-store
    projection. Pinning this keeps the unreachable ``_transaction_rows`` path from
    silently becoming live if an override is ever dropped."""
    provider = REGISTRY["transaction"]
    assert provider.search is providers.search_transaction
    assert provider.resolve is mention_resolve._resolve_transaction


def test_transaction_allowlist_cannot_surface_a_stored_base_amount():
    """Defence in depth: the fields allowlist names the SERVICE output key
    (``amount_base``), not the stored key (``base_amount``). Even if the generic path
    ran, a raw stored base amount has no allowlist slot to be emitted through."""
    from backend.services.mentions._collect import _DESCRIPTORS

    txn = next(d for d in _DESCRIPTORS if d.type == "transaction")
    assert "amount_base" in txn.fields
    assert "base_amount" not in txn.fields
