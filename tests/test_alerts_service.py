"""services.alerts choke point + list/group reads (ADR 0014)."""

from __future__ import annotations

import pytest

from backend.core.errors import NotFoundError, ValidationError


@pytest.fixture
def alerts(db_isolated):
    import backend.services.alerts as svc

    return svc


def test_apply_create_update_delete(alerts):
    created = alerts.apply_alert_change(
        {"type": "create", "ticker": "AAPL", "op": ">=", "value": 150, "note": "trim"}
    )
    assert created["ticker"] == "AAPL" and created["value"] == 150.0

    updated = alerts.apply_alert_change({"type": "update", "id": created["id"], "value": 160})
    assert updated["value"] == 160.0

    deleted = alerts.apply_alert_change({"type": "delete", "id": created["id"]})
    assert deleted == {"id": created["id"], "deleted": True}
    assert alerts.list_alerts() == []


def test_update_missing_raises_not_found(alerts):
    with pytest.raises(NotFoundError):
        alerts.apply_alert_change({"type": "update", "id": "ghost", "value": 1})


def test_delete_missing_raises_not_found(alerts):
    with pytest.raises(NotFoundError):
        alerts.apply_alert_change({"type": "delete", "id": "ghost"})


def test_unknown_change_type_raises(alerts):
    with pytest.raises(ValidationError):
        alerts.apply_alert_change({"type": "frobnicate"})


def test_create_validation_propagates(alerts):
    with pytest.raises(ValidationError):
        alerts.apply_alert_change({"type": "create", "ticker": "AAPL", "op": "??", "value": 1})


def test_list_filter_and_group(alerts):
    alerts.apply_alert_change({"type": "create", "ticker": "AAPL", "op": ">=", "value": 150})
    alerts.apply_alert_change({"type": "create", "ticker": "AAPL", "op": "<=", "value": 100})
    alerts.apply_alert_change({"type": "create", "ticker": "MSFT", "op": ">=", "value": 400})

    assert len(alerts.list_alerts()) == 3
    assert len(alerts.list_alerts("aapl")) == 2  # ticker filter, case-folded
    grouped = alerts.alerts_by_ticker()
    assert set(grouped) == {"AAPL", "MSFT"} and len(grouped["AAPL"]) == 2
