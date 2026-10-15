"""Unit tests for the Phase 2a new mentionable types (generic-provider descriptors).

Covers commitment, intel_signal, and alert — the FIRST production exercises of
make_search / make_resolve / _project with real store shapes.

Per-type checks:
  - generic search returns matching rows with value == id (the stored ref)
  - generic search sublabel has the expected shape
  - generic resolve returns the projected fields (only allowlisted keys)
  - a sensitive field (if any) is omitted from resolve output
  - the forbidden-store guard passes (register() must not raise)
  - empty-store search returns [] without error
  - no-match search returns [] without error

RFC 2026-06-25-lucius-generic-entity-registry §10 Phase 2.
"""

from __future__ import annotations

from unittest.mock import patch

from backend.services.mentions.registry import REGISTRY
from backend.services.mentions.types import SearchQuery

# ── Helpers ───────────────────────────────────────────────────────────────────


def _q(q: str = "", limit: int = 25) -> SearchQuery:
    return SearchQuery(q=q, types=None, surfaces=frozenset({"entity"}), limit=limit, cursor=None)


def _assert_row_shape(row: dict, type_: str) -> None:
    required = {"type", "id", "label", "sublabel", "value", "icon", "surface"}
    assert required <= set(row), f"row missing keys {required - set(row)}: {row}"
    assert row["type"] == type_
    assert row["surface"] == "entity"
    assert row["value"] == row["id"], "value must equal id for store-backed types"


# ── commitment ────────────────────────────────────────────────────────────────

_COMMITMENT_ROW = {
    "id": "riverside-heights",
    "name": "Parkview Residences Apartment",
    "kind": "purchase",
    "currency": "VND",
    "status": "active",
    "linked_position_id": None,
    "linked_account_id": None,
    "note": "Phase 1 payment plan",
    "created_at": "2026-01-15",
    "installments": [{"id": "inst-1", "label": "Booking", "amount": 5000000, "status": "paid"}],
    "funding_plan": [],
    "actions": [],
}


def _commitment_descriptor():
    return REGISTRY["commitment"]


def test_commitment_in_registry():
    assert "commitment" in REGISTRY


def test_commitment_search_returns_matching_row():
    desc = REGISTRY["commitment"]
    with patch(
        "backend.db.commitments_store.CommitmentsStore.all",
        return_value=[_COMMITMENT_ROW],
    ):
        rows = desc.search(_q(q="parkview"))
    assert len(rows) == 1
    _assert_row_shape(rows[0], "commitment")
    assert rows[0]["label"] == "Parkview Residences Apartment"
    assert rows[0]["id"] == "riverside-heights"
    assert rows[0]["value"] == "riverside-heights"


def test_commitment_search_empty_store_returns_empty():
    desc = REGISTRY["commitment"]
    with patch(
        "backend.db.commitments_store.CommitmentsStore.all",
        return_value=[],
    ):
        rows = desc.search(_q(q=""))
    assert rows == []


def test_commitment_search_no_match_returns_empty():
    desc = REGISTRY["commitment"]
    with patch(
        "backend.db.commitments_store.CommitmentsStore.all",
        return_value=[_COMMITMENT_ROW],
    ):
        rows = desc.search(_q(q="xyz_no_match_999"))
    assert rows == []


def test_commitment_search_sublabel_format():
    desc = REGISTRY["commitment"]
    with patch(
        "backend.db.commitments_store.CommitmentsStore.all",
        return_value=[_COMMITMENT_ROW],
    ):
        rows = desc.search(_q(q=""))
    assert len(rows) == 1
    assert rows[0]["sublabel"] == "purchase • active"


def test_commitment_resolve_projects_allowlisted_fields_only():
    desc = REGISTRY["commitment"]
    with patch(
        "backend.db.commitments_store.CommitmentsStore.all",
        return_value=[_COMMITMENT_ROW],
    ):
        result = desc.resolve("riverside-heights")
    assert result is not None
    # Only allowlisted top-level scalar fields
    assert result.get("name") == "Parkview Residences Apartment"
    assert result.get("kind") == "purchase"
    assert result.get("currency") == "VND"
    assert result.get("status") == "active"
    assert result.get("note") == "Phase 1 payment plan"
    assert result.get("created_at") == "2026-01-15"
    # Nested arrays must NOT appear
    assert "installments" not in result
    assert "funding_plan" not in result
    assert "actions" not in result
    # FK ids must NOT appear
    assert "linked_position_id" not in result
    assert "linked_account_id" not in result


def test_commitment_resolve_not_found_returns_none():
    desc = REGISTRY["commitment"]
    with patch(
        "backend.db.commitments_store.CommitmentsStore.all",
        return_value=[_COMMITMENT_ROW],
    ):
        result = desc.resolve("does-not-exist")
    assert result is None


def test_commitment_search_searches_kind_field():
    """search_fields includes 'kind' — searching by kind must match."""
    desc = REGISTRY["commitment"]
    with patch(
        "backend.db.commitments_store.CommitmentsStore.all",
        return_value=[_COMMITMENT_ROW],
    ):
        rows = desc.search(_q(q="purchase"))
    assert len(rows) == 1
    assert rows[0]["id"] == "riverside-heights"


def test_commitment_search_searches_note_field():
    """search_fields includes 'note' — searching by note must match."""
    desc = REGISTRY["commitment"]
    with patch(
        "backend.db.commitments_store.CommitmentsStore.all",
        return_value=[_COMMITMENT_ROW],
    ):
        rows = desc.search(_q(q="payment plan"))
    assert len(rows) == 1


# ── intel_signal ─────────────────────────────────────────────────────────────

_INTEL_SIGNAL_ROW = {
    "id": "sig-abc123",
    "created_at": "2026-06-25T10:00:00",
    "type": "news",
    "severity": "warning",
    "title": "AAPL drops 15% after earnings miss",
    "summary": "Apple reported Q2 EPS below consensus; shares fell sharply in after-hours.",
    "portfolio_impact": "Your AAPL holding represents 8% of portfolio.",
    "action_items": ["Review position size", "Consider stop-loss"],
    "affected_tickers": ["AAPL"],
    "sources": [{"title": "Reuters", "url": "https://reuters.com/aapl"}],
    "source_urls": ["https://reuters.com/aapl"],
    "read": False,
    "trigger": "crash_watch",
}


def test_intel_signal_in_registry():
    assert "notification" in REGISTRY


def test_intel_signal_search_returns_matching_row():
    desc = REGISTRY["notification"]
    with patch(
        "backend.db.notification_log_store.NotificationLogStore.query_signals",
        return_value=[_INTEL_SIGNAL_ROW],
    ):
        rows = desc.search(_q(q="aapl"))
    assert len(rows) == 1
    _assert_row_shape(rows[0], "notification")
    assert rows[0]["label"] == "AAPL drops 15% after earnings miss"
    assert rows[0]["id"] == "sig-abc123"
    assert rows[0]["value"] == "sig-abc123"


def test_intel_signal_search_empty_store_returns_empty():
    desc = REGISTRY["notification"]
    with patch(
        "backend.db.notification_log_store.NotificationLogStore.query_signals",
        return_value=[],
    ):
        rows = desc.search(_q(q=""))
    assert rows == []


def test_intel_signal_search_no_match_returns_empty():
    desc = REGISTRY["notification"]
    with patch(
        "backend.db.notification_log_store.NotificationLogStore.query_signals",
        return_value=[_INTEL_SIGNAL_ROW],
    ):
        rows = desc.search(_q(q="xyz_no_match_999"))
    assert rows == []


def test_intel_signal_search_sublabel_format():
    desc = REGISTRY["notification"]
    with patch(
        "backend.db.notification_log_store.NotificationLogStore.query_signals",
        return_value=[_INTEL_SIGNAL_ROW],
    ):
        rows = desc.search(_q(q=""))
    assert len(rows) == 1
    assert rows[0]["sublabel"] == "warning • news"


def test_intel_signal_resolve_projects_allowlisted_fields_only():
    # resolve goes through find_one → find_by_id (not query_signals) so that
    # signals at positions 51-200 are still resolvable (fix for critical finding).
    desc = REGISTRY["notification"]
    with patch(
        "backend.db.notification_log_store.NotificationLogStore.find_by_id",
        return_value=_INTEL_SIGNAL_ROW,
    ):
        result = desc.resolve("sig-abc123")
    assert result is not None
    assert result.get("title") == "AAPL drops 15% after earnings miss"
    assert result.get("summary") is not None
    assert result.get("type") == "news"
    assert result.get("severity") == "warning"
    assert result.get("portfolio_impact") is not None
    assert result.get("created_at") is not None
    # Excluded arrays must NOT appear
    assert "sources" not in result
    assert "source_urls" not in result
    assert "affected_tickers" not in result
    assert "action_items" not in result


def test_intel_signal_resolve_not_found_returns_none():
    desc = REGISTRY["notification"]
    with patch(
        "backend.db.notification_log_store.NotificationLogStore.find_by_id",
        return_value=None,
    ):
        result = desc.resolve("does-not-exist")
    assert result is None


def test_intel_signal_search_searches_summary_field():
    """search_fields includes 'summary' — searching by summary keyword must match."""
    desc = REGISTRY["notification"]
    with patch(
        "backend.db.notification_log_store.NotificationLogStore.query_signals",
        return_value=[_INTEL_SIGNAL_ROW],
    ):
        rows = desc.search(_q(q="earnings miss"))
    assert len(rows) == 1


def test_intel_signal_search_searches_type_field():
    """search_fields includes 'type' — searching by type must match."""
    desc = REGISTRY["notification"]
    with patch(
        "backend.db.notification_log_store.NotificationLogStore.query_signals",
        return_value=[_INTEL_SIGNAL_ROW],
    ):
        rows = desc.search(_q(q="news"))
    assert len(rows) == 1


# ── alert ─────────────────────────────────────────────────────────────────────

_ALERT_ROW = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "ticker": "TSLA",
    "instrument_id": "inst-tsla",
    "op": ">=",
    "value": 200.0,
    "note": "Breakout target",
    "label": "TSLA breakout",
    "asset_class": "equity",
    "created_at": "2026-06-01T09:00:00+00:00",
}


def test_alert_in_registry():
    assert "alert" in REGISTRY


def test_alert_search_returns_matching_row():
    desc = REGISTRY["alert"]
    with patch(
        "backend.db.alert_store.AlertStore.all",
        return_value=[_ALERT_ROW],
    ):
        rows = desc.search(_q(q="tsla"))
    assert len(rows) == 1
    _assert_row_shape(rows[0], "alert")
    # label_field="ticker"
    assert rows[0]["label"] == "TSLA"
    assert rows[0]["id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert rows[0]["value"] == "550e8400-e29b-41d4-a716-446655440000"


def test_alert_search_empty_store_returns_empty():
    desc = REGISTRY["alert"]
    with patch(
        "backend.db.alert_store.AlertStore.all",
        return_value=[],
    ):
        rows = desc.search(_q(q=""))
    assert rows == []


def test_alert_search_no_match_returns_empty():
    desc = REGISTRY["alert"]
    with patch(
        "backend.db.alert_store.AlertStore.all",
        return_value=[_ALERT_ROW],
    ):
        rows = desc.search(_q(q="xyz_no_match_999"))
    assert rows == []


def test_alert_search_sublabel_format():
    desc = REGISTRY["alert"]
    with patch(
        "backend.db.alert_store.AlertStore.all",
        return_value=[_ALERT_ROW],
    ):
        rows = desc.search(_q(q=""))
    assert len(rows) == 1
    assert rows[0]["sublabel"] == ">= 200.0"


def test_alert_resolve_projects_allowlisted_fields_only():
    desc = REGISTRY["alert"]
    with patch(
        "backend.db.alert_store.AlertStore.all",
        return_value=[_ALERT_ROW],
    ):
        result = desc.resolve("550e8400-e29b-41d4-a716-446655440000")
    assert result is not None
    assert result.get("ticker") == "TSLA"
    assert result.get("op") == ">="
    assert result.get("value") == 200.0
    assert result.get("label") == "TSLA breakout"
    assert result.get("asset_class") == "equity"
    assert result.get("note") == "Breakout target"
    assert result.get("created_at") is not None
    # FK id must NOT appear
    assert "instrument_id" not in result


def test_alert_resolve_not_found_returns_none():
    desc = REGISTRY["alert"]
    with patch(
        "backend.db.alert_store.AlertStore.all",
        return_value=[_ALERT_ROW],
    ):
        result = desc.resolve("does-not-exist")
    assert result is None


def test_alert_search_searches_label_field():
    """search_fields includes 'label' — the human label field must be searched."""
    desc = REGISTRY["alert"]
    with patch(
        "backend.db.alert_store.AlertStore.all",
        return_value=[_ALERT_ROW],
    ):
        rows = desc.search(_q(q="breakout"))
    assert len(rows) == 1


def test_alert_search_searches_note_field():
    """search_fields includes 'note' — searching by note must match."""
    desc = REGISTRY["alert"]
    with patch(
        "backend.db.alert_store.AlertStore.all",
        return_value=[_ALERT_ROW],
    ):
        rows = desc.search(_q(q="target"))
    assert len(rows) == 1


# ── income (Phase 2b) ──────────────────────────────────────────────────────────
# IncomeEventStore rows carry instrument_id; the descriptor's rows lambda joins the
# instrument symbol via InstrumentStore — both stores are mocked here.

_INSTRUMENT_ROWS = [
    {"id": "inst-aapl", "symbol": "AAPL", "name": "Apple Inc."},
    {"id": "inst-tsla", "symbol": "TSLA", "name": "Tesla Inc."},
]

_INCOME_ROW = {
    "id": "inc-001",
    "position_id": "pos-aapl",
    "instrument_id": "inst-aapl",
    "account_id": "acct-vantage",
    "paid_at": "2026-03-15",
    "income_kind": "dividend",
    "quantity": 100.0,
    "amount_per_unit": 0.24,
    "gross_amount": 24.0,
    "tax_withheld": 3.6,
    "currency": "USD",
    "is_drp": False,
    "drp_lot_id": None,
    "txn_id": "txn-xyz",
    "base_amount": 32.4,
    "base_currency": "SGD",
    "fx_rate": 1.35,
    "idempotency_key": "broker-div-001",
    "source": "manual",
    "note": "Q1 dividend",
    "created_at": "2026-03-16T10:00:00",
}


def test_income_in_registry():
    assert "income" in REGISTRY


def test_income_search_label_is_symbol():
    desc = REGISTRY["income"]
    with (
        patch("backend.db.income_event_store.IncomeEventStore.all", return_value=[_INCOME_ROW]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=_INSTRUMENT_ROWS),
    ):
        rows = desc.search(_q(q="aapl"))
    assert len(rows) == 1
    _assert_row_shape(rows[0], "income")
    assert rows[0]["label"] == "AAPL"  # label_field="symbol" (denormalised)
    assert rows[0]["id"] == "inc-001"
    assert rows[0]["value"] == "inc-001"
    assert rows[0]["sublabel"] == "dividend • 2026-03-15"


def test_income_search_empty_store_returns_empty():
    desc = REGISTRY["income"]
    with (
        patch("backend.db.income_event_store.IncomeEventStore.all", return_value=[]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=[]),
    ):
        assert desc.search(_q(q="")) == []


def test_income_resolve_excludes_fk_and_stale_base():
    desc = REGISTRY["income"]
    with (
        patch("backend.db.income_event_store.IncomeEventStore.all", return_value=[_INCOME_ROW]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=_INSTRUMENT_ROWS),
    ):
        result = desc.resolve("inc-001")
    assert result is not None
    assert result.get("symbol") == "AAPL"
    assert result.get("income_kind") == "dividend"
    assert result.get("gross_amount") == 24.0
    assert result.get("currency") == "USD"
    # Stale write-time base snapshot + fx + FK ids EXCLUDED
    assert "base_amount" not in result
    assert "base_currency" not in result
    assert "fx_rate" not in result
    assert "instrument_id" not in result
    assert "position_id" not in result
    assert "account_id" not in result
    assert "txn_id" not in result
    assert "idempotency_key" not in result


def test_income_search_searches_kind():
    desc = REGISTRY["income"]
    with (
        patch("backend.db.income_event_store.IncomeEventStore.all", return_value=[_INCOME_ROW]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=_INSTRUMENT_ROWS),
    ):
        assert len(desc.search(_q(q="dividend"))) == 1


# ── lot (Phase 2b) ───────────────────────────────────────────────────────────--

_LOT_ROW = {
    "id": "lot-001",
    "position_id": "pos-aapl",
    "instrument_id": "inst-aapl",
    "account_id": "acct-vantage",
    "acquired_at": "2025-01-10",
    "original_qty": 100.0,
    "remaining_qty": 60.0,
    "unit_cost": 150.0,
    "cost_currency": "USD",
    "fees": 1.0,
    "kind": "buy",
    "txn_id": "txn-buy",
    "idempotency_key": "order-123",
    "description": "Initial buy",
    "created_at": "2025-01-10T09:00:00",
    "updated_at": "2025-06-01T09:00:00",
}


def test_lot_in_registry():
    assert "lot" in REGISTRY


def test_lot_search_label_and_sublabel():
    desc = REGISTRY["lot"]
    with (
        patch("backend.db.lot_store.LotStore.all", return_value=[_LOT_ROW]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=_INSTRUMENT_ROWS),
    ):
        rows = desc.search(_q(q="aapl"))
    assert len(rows) == 1
    _assert_row_shape(rows[0], "lot")
    assert rows[0]["label"] == "AAPL"
    assert rows[0]["sublabel"] == "buy • 60.0 left • 2025-01-10"


def test_lot_resolve_excludes_fk_and_idempotency():
    desc = REGISTRY["lot"]
    with (
        patch("backend.db.lot_store.LotStore.all", return_value=[_LOT_ROW]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=_INSTRUMENT_ROWS),
    ):
        result = desc.resolve("lot-001")
    assert result is not None
    assert result.get("symbol") == "AAPL"
    assert result.get("kind") == "buy"
    assert result.get("original_qty") == 100.0
    assert result.get("remaining_qty") == 60.0
    assert result.get("unit_cost") == 150.0
    assert "position_id" not in result
    assert "instrument_id" not in result
    assert "account_id" not in result
    assert "txn_id" not in result
    assert "idempotency_key" not in result


def test_lot_search_empty_store_returns_empty():
    desc = REGISTRY["lot"]
    with (
        patch("backend.db.lot_store.LotStore.all", return_value=[]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=[]),
    ):
        assert desc.search(_q(q="")) == []


# ── grant (Phase 2b) ───────────────────────────────────────────────────────────

_GRANT_ROW = {
    "id": "grant-001",
    "type": "rsu",
    "instrument_id": "inst-tsla",
    "grant_date": "2024-06-01",
    "total_units": 400.0,
    "currency": "USD",
    "tax_event": "at_vest",
    "status": "active",
    "locked_position_id": "pos-locked",
    "account_id": "acct-vantage",
    "tranches": [{"id": "tr-1", "vest_date": "2025-06-01", "units": 100.0, "vested": True}],
    "note": "New hire grant",
    "created_at": "2024-06-01T00:00:00",
    "updated_at": "2024-06-01T00:00:00",
}


def test_grant_in_registry():
    assert "grant" in REGISTRY


def test_grant_search_label_and_sublabel():
    desc = REGISTRY["grant"]
    with (
        patch("backend.db.grant_store.GrantStore.all", return_value=[_GRANT_ROW]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=_INSTRUMENT_ROWS),
    ):
        rows = desc.search(_q(q="tsla"))
    assert len(rows) == 1
    _assert_row_shape(rows[0], "grant")
    assert rows[0]["label"] == "TSLA"
    assert rows[0]["sublabel"] == "RSU • active"


def test_grant_resolve_excludes_nested_tranches_and_fk():
    desc = REGISTRY["grant"]
    with (
        patch("backend.db.grant_store.GrantStore.all", return_value=[_GRANT_ROW]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=_INSTRUMENT_ROWS),
    ):
        result = desc.resolve("grant-001")
    assert result is not None
    assert result.get("symbol") == "TSLA"
    assert result.get("type") == "rsu"
    assert result.get("total_units") == 400.0
    assert result.get("status") == "active"
    # Nested array + FK ids EXCLUDED
    assert "tranches" not in result
    assert "instrument_id" not in result
    assert "locked_position_id" not in result
    assert "account_id" not in result


def test_grant_search_searches_type():
    desc = REGISTRY["grant"]
    with (
        patch("backend.db.grant_store.GrantStore.all", return_value=[_GRANT_ROW]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=_INSTRUMENT_ROWS),
    ):
        assert len(desc.search(_q(q="rsu"))) == 1


# ── vest (Phase 2b) ────────────────────────────────────────────────────────────
# vest_events carry grant_id (not instrument_id) — the rows lambda hops
# grant → instrument → symbol, so BOTH GrantStore and InstrumentStore are mocked.

_VEST_ROW = {
    "id": "vest-001",
    "grant_id": "grant-001",
    "tranche_id": "tr-1",
    "date": "2025-06-01",
    "units": 100.0,
    "fmv_per_unit": 250.0,
    "fmv_currency": "USD",
    "price_paid_per_unit": 0.0,
    "units_withheld": 35.0,
    "gross_gain_native": 25000.0,
    "txn_id": "txn-vest",
    "lot_id": "lot-vest",
    "locked_position_id": "pos-locked",
    "idempotency_key": "vest-key-001",
    "source": "manual",
    "base_amount": 33750.0,
    "base_currency": "SGD",
    "note": "First tranche vest",
    "created_at": "2025-06-01T00:00:00",
}


def test_vest_in_registry():
    assert "vest" in REGISTRY


def test_vest_search_label_via_grant_hop():
    desc = REGISTRY["vest"]
    with (
        patch("backend.db.vest_event_store.VestEventStore.all", return_value=[_VEST_ROW]),
        patch("backend.db.grant_store.GrantStore.all", return_value=[_GRANT_ROW]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=_INSTRUMENT_ROWS),
    ):
        rows = desc.search(_q(q="tsla"))
    assert len(rows) == 1
    _assert_row_shape(rows[0], "vest")
    assert rows[0]["label"] == "TSLA"  # vest → grant → instrument → symbol
    assert rows[0]["sublabel"] == "100.0 units • 2025-06-01"


def test_vest_resolve_excludes_snapshot_and_fk():
    desc = REGISTRY["vest"]
    with (
        patch("backend.db.vest_event_store.VestEventStore.all", return_value=[_VEST_ROW]),
        patch("backend.db.grant_store.GrantStore.all", return_value=[_GRANT_ROW]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=_INSTRUMENT_ROWS),
    ):
        result = desc.resolve("vest-001")
    assert result is not None
    assert result.get("symbol") == "TSLA"
    assert result.get("units") == 100.0
    assert result.get("fmv_per_unit") == 250.0
    # Recomputed-by-report snapshot + stale base + FK ids EXCLUDED
    assert "gross_gain_native" not in result
    assert "base_amount" not in result
    assert "base_currency" not in result
    assert "grant_id" not in result
    assert "tranche_id" not in result
    assert "txn_id" not in result
    assert "lot_id" not in result
    assert "locked_position_id" not in result
    assert "idempotency_key" not in result


def test_vest_search_empty_store_returns_empty():
    desc = REGISTRY["vest"]
    with (
        patch("backend.db.vest_event_store.VestEventStore.all", return_value=[]),
        patch("backend.db.grant_store.GrantStore.all", return_value=[]),
        patch("backend.db.instrument_store.InstrumentStore.all", return_value=[]),
    ):
        assert desc.search(_q(q="")) == []


# ── Cross-cutting: all Phase 2 generic-provider types ──────────────────────────

_PHASE2_TYPES = (
    "commitment",
    "notification",
    "alert",
    "income",
    "lot",
    "grant",
    "vest",
)


def test_new_types_pass_forbidden_store_guard():
    """The registry import must succeed — register() passes the forbidden-store check
    for all Phase 2 descriptors."""
    for type_ in _PHASE2_TYPES:
        assert type_ in REGISTRY, f"{type_} must be in REGISTRY"
        p = REGISTRY[type_]
        assert callable(p.search)
        assert callable(p.resolve)


def test_new_types_have_entity_surface():
    """All Phase 2 descriptors must appear on the entity surface only."""
    for type_ in _PHASE2_TYPES:
        desc = REGISTRY[type_]
        assert desc.surfaces == frozenset({"entity"})


def test_new_types_no_sensitive_fields():
    """None of the Phase 2 descriptors have sensitive fields — all data is display-safe."""
    from backend.services.mentions._collect import _DESCRIPTORS

    for d in _DESCRIPTORS:
        if d.type in _PHASE2_TYPES:
            for key, spec in d.fields.items():
                assert not spec.sensitive, (
                    f"{d.type}.{key} is marked sensitive — verify this is intentional"
                )


def test_new_types_use_generic_provider_no_overrides():
    """Phase 2 types must use the GENERIC provider — no bespoke search/resolve overrides.

    This is the whole point of Phase 2: prove make_search / make_resolve / _project
    work end-to-end on real stores without per-type functions.
    """
    from backend.services.mentions._collect import _DESCRIPTORS

    for d in _DESCRIPTORS:
        if d.type in _PHASE2_TYPES:
            assert d.search_override is None, f"{d.type} must not set search_override"
            assert d.resolve_override is None, f"{d.type} must not set resolve_override"
            assert d.project_fn is None, f"{d.type} must not set project_fn (use _project)"
