"""A share transfer between two of the user's accounts is a non-taxable disposal that
ALSO names where the shares went (``transfer_to_account_id``) — the fact the Activity feed
needs to show "1242 units → IBKR" instead of an anonymous "transferred out". The choke
stores the destination; it does NOT mint the receiving lots (ADR
2026-09-05-transfer-destination-is-a-label-not-a-leg).

Refusals live in ``compute_disposal`` (pure, before any write): a destination only makes
sense on a movement of shares, not money — so it requires ``is_taxable=false``, zero
proceeds and no ``proceeds_account_id``. Resolution (id OR exact name, must exist, must
differ from the source) is the shared pre-step both preview and apply run.
"""

from __future__ import annotations

import pytest

from backend.core.errors import InvalidLotSelection, ValidationError
from backend.db.account_store import AccountStore
from backend.db.realized_event_store import RealizedEventStore
from backend.services import lots as svc

_USD = {"USD": 1.0}
_POS = {"id": "pos-1", "instrument_id": "inst-1", "account_id": "acc-src", "cost_currency": "USD"}


def _lot(qty=100.0):
    return {
        "id": "lot-1",
        "position_id": "pos-1",
        "instrument_id": "inst-1",
        "account_id": "acc-src",
        "acquired_at": "2025-01-01",
        "original_qty": qty,
        "remaining_qty": qty,
        "unit_cost": 40.0,
        "cost_currency": "USD",
        "fees": 0.0,
        "kind": "buy",
        "created_at": "2025-01-01T00:00:00Z",
    }


def _move(**kw):
    d = {
        "quantity": 10,
        "proceeds_per_unit": 0,
        "proceeds_currency": "USD",
        "disposed_at": "2026-06-01",
        "is_taxable": False,
        "transfer_to_account_id": "acc-dst",
    }
    d.update(kw)
    return d


# ── compute_disposal (pure) ────────────────────────────────────────────────────


def test_draft_carries_the_destination():
    draft = svc.compute_disposal(_POS, [_lot()], _move(), rates=_USD, base="USD")
    assert draft["is_taxable"] is False
    assert draft["transfer_to_account_id"] == "acc-dst"
    assert draft["realized_gain"] is None


def test_a_plain_sale_stores_no_destination():
    draft = svc.compute_disposal(
        _POS,
        [_lot()],
        _move(is_taxable=True, proceeds_per_unit=50, transfer_to_account_id=None),
        rates=_USD,
        base="USD",
    )
    assert draft["transfer_to_account_id"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"is_taxable": True, "proceeds_per_unit": 50},  # a sale with a destination
        {"proceeds_per_unit": 50},  # non-taxable but money changed hands
        {"proceeds_account_id": "acc-cash"},  # a cash deposit on a share move
    ],
)
def test_a_destination_needs_a_pure_share_movement(bad):
    with pytest.raises(InvalidLotSelection, match="moves shares, not money"):
        svc.compute_disposal(_POS, [_lot()], _move(**bad), rates=_USD, base="USD")


# ── resolution (store-backed pre-step) ─────────────────────────────────────────


def _seed(monkeypatch):
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore

    monkeypatch.setattr(svc, "_load_rates", lambda: _USD)
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    src = AccountStore().create(
        {"name": "Workplace Custodian", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    dst = AccountStore().create(
        {"name": "IBKR", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    pos = PositionStore().create(
        {
            "instrument_id": "inst-1",
            "account_id": src["id"],
            "quantity": 100.0,
            "cost_basis": 40.0,
            "cost_currency": "USD",
            "bucket": "liquid",
        }
    )
    LotStore().create({**_lot(), "id": None, "position_id": pos["id"], "account_id": src["id"]})
    return src, dst, pos


def test_destination_resolves_by_name_and_is_stored(tmp_path, monkeypatch):
    src, dst, pos = _seed(monkeypatch)
    out = svc.apply_lot_change(
        {"type": "dispose", "position_id": pos["id"], **_move(transfer_to_account_id="ibkr")}
    )
    assert out["ok"], out
    ev = RealizedEventStore().find_by_id(out["event_id"])
    assert ev["is_taxable"] is False
    assert ev["transfer_to_account_id"] == dst["id"]
    assert ev["account_id"] == src["id"]
    assert out["transfer_to_account_id"] == dst["id"]


def test_preview_resolves_the_destination_too(tmp_path, monkeypatch):
    src, dst, pos = _seed(monkeypatch)
    draft = svc.preview_disposal(
        {"type": "dispose", "position_id": pos["id"], **_move(transfer_to_account_id=dst["id"])}
    )
    assert draft["transfer_to_account_id"] == dst["id"]
    assert RealizedEventStore().query_events(position_id=pos["id"]) == []


def test_unknown_destination_is_refused_before_any_write(tmp_path, monkeypatch):
    src, dst, pos = _seed(monkeypatch)
    with pytest.raises(ValidationError, match="Unknown account 'Nowhere'"):
        svc.apply_lot_change(
            {"type": "dispose", "position_id": pos["id"], **_move(transfer_to_account_id="Nowhere")}
        )
    assert RealizedEventStore().query_events(position_id=pos["id"]) == []


def test_destination_must_differ_from_the_source(tmp_path, monkeypatch):
    src, dst, pos = _seed(monkeypatch)
    with pytest.raises(ValidationError, match="already holds"):
        svc.apply_lot_change(
            {"type": "dispose", "position_id": pos["id"], **_move(transfer_to_account_id=src["id"])}
        )
    assert RealizedEventStore().query_events(position_id=pos["id"]) == []
