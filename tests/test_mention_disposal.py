"""Unit tests for the ``disposal`` mentionable type (generic-provider descriptor).

Mirrors the commitment/alert coverage in test_mention_new_types.py for the new
``disposal`` descriptor (equity_lots.py, order=25) backed by RealizedEventStore:
  - it's registered (register() ran its fail-closed guards without raising)
  - generic search returns matching rows (value == id, label == denormalised symbol)
  - the sublabel has the "<qty> units • <disposed_at>" shape
  - resolve projects ONLY the allowlisted native fields
  - the stale base snapshots (realized_gain_base / base_currency / fx_rate), the nested
    consumptions audit array, the FK ids, and the idempotency key are all OMITTED
  - not-found resolve / empty-store search degrade to None / [] without error
"""

from __future__ import annotations

from unittest.mock import patch

from backend.services.mentions.registry import REGISTRY
from backend.services.mentions.types import SearchQuery

_STORE = "backend.db.realized_event_store.RealizedEventStore.all"
_INSTRUMENTS = "backend.db.instrument_store.InstrumentStore.all"

_DISPOSAL_ROW = {
    "id": "rlz-1",
    "position_id": "pos-1",
    "instrument_id": "inst-1",
    "account_id": "acct-1",
    "disposed_at": "2024-02-20",
    "quantity": 15,
    "proceeds_per_unit": 688,
    "proceeds_currency": "USD",
    "disposal_fees": 2.0,
    "method": "fifo",
    "is_taxable": True,
    "cost_basis_known": True,
    "consumptions": [{"lot_id": "lot-1", "qty": 15}],
    "realized_gain": 5000,
    "realized_gain_base": 6750,
    "base_currency": "SGD",
    "fx_rate": 1.35,
    "txn_id": "txn-1",
    "idempotency_key": "idem-1",
    "source": "manual",
    "note": "sold half",
    "created_at": "2024-02-20",
}

_INSTRUMENT_ROWS = [{"id": "inst-1", "symbol": "AAPL"}]


def _q(q: str = "", limit: int = 25) -> SearchQuery:
    return SearchQuery(q=q, types=None, surfaces=frozenset({"entity"}), limit=limit, cursor=None)


def _assert_row_shape(row: dict) -> None:
    required = {"type", "id", "label", "sublabel", "value", "icon", "surface"}
    assert required <= set(row), f"row missing keys {required - set(row)}: {row}"
    assert row["type"] == "disposal"
    assert row["surface"] == "entity"
    assert row["value"] == row["id"], "value must equal id for store-backed types"


def test_disposal_in_registry():
    assert "disposal" in REGISTRY
    assert REGISTRY["disposal"].surfaces == frozenset({"entity"})


def test_disposal_search_returns_matching_row_labelled_by_symbol():
    desc = REGISTRY["disposal"]
    with (
        patch(_STORE, return_value=[_DISPOSAL_ROW]),
        patch(_INSTRUMENTS, return_value=_INSTRUMENT_ROWS),
    ):
        rows = desc.search(_q(q="aapl"))
    assert len(rows) == 1
    _assert_row_shape(rows[0])
    assert rows[0]["label"] == "AAPL"  # denormalised from instrument_id, not the bare id
    assert rows[0]["id"] == "rlz-1"
    assert rows[0]["value"] == "rlz-1"


def test_disposal_search_sublabel_format():
    desc = REGISTRY["disposal"]
    with (
        patch(_STORE, return_value=[_DISPOSAL_ROW]),
        patch(_INSTRUMENTS, return_value=_INSTRUMENT_ROWS),
    ):
        rows = desc.search(_q(q=""))
    assert len(rows) == 1
    assert rows[0]["sublabel"] == "15 units • 2024-02-20"


def test_disposal_search_matches_method_field():
    """search_fields includes 'method' — searching by the lot method must match."""
    desc = REGISTRY["disposal"]
    with (
        patch(_STORE, return_value=[_DISPOSAL_ROW]),
        patch(_INSTRUMENTS, return_value=_INSTRUMENT_ROWS),
    ):
        rows = desc.search(_q(q="fifo"))
    assert len(rows) == 1
    assert rows[0]["id"] == "rlz-1"


def test_disposal_search_empty_store_returns_empty():
    desc = REGISTRY["disposal"]
    with patch(_STORE, return_value=[]), patch(_INSTRUMENTS, return_value=_INSTRUMENT_ROWS):
        assert desc.search(_q(q="")) == []


def test_disposal_search_no_match_returns_empty():
    desc = REGISTRY["disposal"]
    with (
        patch(_STORE, return_value=[_DISPOSAL_ROW]),
        patch(_INSTRUMENTS, return_value=_INSTRUMENT_ROWS),
    ):
        assert desc.search(_q(q="zzz_no_match_999")) == []


def test_disposal_resolve_projects_allowlisted_native_fields_only():
    desc = REGISTRY["disposal"]
    with (
        patch(_STORE, return_value=[_DISPOSAL_ROW]),
        patch(_INSTRUMENTS, return_value=_INSTRUMENT_ROWS),
    ):
        result = desc.resolve("rlz-1")
    assert result is not None
    # Allowlisted native display fields present
    assert result.get("symbol") == "AAPL"
    assert result.get("disposed_at") == "2024-02-20"
    assert result.get("quantity") == 15
    assert result.get("proceeds_per_unit") == 688
    assert result.get("proceeds_currency") == "USD"
    assert result.get("disposal_fees") == 2.0
    assert result.get("method") == "fifo"
    assert result.get("is_taxable") is True
    assert result.get("cost_basis_known") is True
    assert result.get("realized_gain") == 5000  # NATIVE (proceeds_currency), not the base snapshot
    assert result.get("note") == "sold half"
    assert result.get("created_at") == "2024-02-20"


def test_disposal_resolve_excludes_base_snapshots_nested_and_fks():
    desc = REGISTRY["disposal"]
    with (
        patch(_STORE, return_value=[_DISPOSAL_ROW]),
        patch(_INSTRUMENTS, return_value=_INSTRUMENT_ROWS),
    ):
        result = desc.resolve("rlz-1")
    assert result is not None
    # Stale base snapshots are NEVER emitted (the "never trust stored base_amount" invariant).
    for base_key in ("realized_gain_base", "base_currency", "fx_rate"):
        assert base_key not in result, f"{base_key} leaked into disposal resolve output"
    # Nested audit array + FK ids + idempotency key are not model-display fields.
    for excluded in (
        "consumptions",
        "position_id",
        "instrument_id",
        "account_id",
        "txn_id",
        "idempotency_key",
    ):
        assert excluded not in result


def test_disposal_resolve_not_found_returns_none():
    desc = REGISTRY["disposal"]
    with (
        patch(_STORE, return_value=[_DISPOSAL_ROW]),
        patch(_INSTRUMENTS, return_value=_INSTRUMENT_ROWS),
    ):
        assert desc.resolve("does-not-exist") is None
