import pytest

from backend.core.errors import ValidationError
from backend.db import account_store
from backend.db.account_store import AccountStore
from backend.models.account import (
    ACCOUNT_BAND,
    LIABILITY_TYPES,
    ON_BUDGET_TYPES,
    SPENDABLE_TYPES,
    AccountType,
    band_of,
    kind_of,
)

SEVEN = {"cash", "fixed_deposit", "investment", "retirement", "receivable", "credit", "loan"}


def test_enum_is_exactly_seven():
    assert {e.value for e in AccountType} == SEVEN
    assert account_store.TYPES == SEVEN


@pytest.mark.parametrize(
    "t,kind",
    [
        ("cash", "asset"),
        ("fixed_deposit", "asset"),
        ("investment", "asset"),
        ("retirement", "asset"),
        ("receivable", "asset"),
        ("credit", "liability"),
        ("loan", "liability"),
    ],
)
def test_kind_is_derived(t, kind):
    assert kind_of(t) == kind
    assert (t in LIABILITY_TYPES) == (kind == "liability")


def test_band_table_covers_every_type():
    assert set(ACCOUNT_BAND) == SEVEN
    assert band_of("investment") == "settlement_cash"
    assert band_of("cash") == "cash"
    assert ON_BUDGET_TYPES == {"cash", "credit"}
    assert SPENDABLE_TYPES == {"cash"}


def test_unknown_type_raises():
    with pytest.raises(ValueError):
        kind_of("checking")


def test_store_refuses_unknown_type(db_isolated):
    with pytest.raises(ValidationError):
        AccountStore().create({"name": "X", "type": "checking", "currency": "SGD"})


def test_store_derives_kind_and_drops_dead_fields(db_isolated):
    a = AccountStore().create(
        {
            "name": "Card",
            "type": "credit",
            "currency": "SGD",
            "kind": "asset",
            "asset_class": "property",
            "locked": True,
            "on_budget": False,
            "group": "cash",
        }
    )
    assert a["kind"] == "liability"
    for k in ("asset_class", "locked", "on_budget", "group"):
        assert k not in a


def test_store_refuses_type_updates_after_creation(db_isolated):
    s = AccountStore()
    a = s.create({"name": "A", "type": "cash", "currency": "SGD"})
    with pytest.raises(ValidationError, match="cannot be changed"):
        s.update_by_id(a["id"], {"type": "loan"})
    assert s.find_by_id(a["id"])["type"] == "cash"


def test_store_refuses_currency_updates_after_creation(db_isolated):
    s = AccountStore()
    a = s.create({"name": "A", "type": "cash", "currency": "SGD"})
    with pytest.raises(ValidationError, match="currency.*cannot be changed"):
        s.update_by_id(a["id"], {"currency": "USD"})
    assert s.find_by_id(a["id"])["currency"] == "SGD"


def test_put_account_refuses_type_changes(db_isolated):
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    created = client.post(
        "/api/accounts",
        json={"name": "DBS", "type": "cash", "currency": "SGD", "initial_balance": 0},
    )
    assert created.status_code == 200
    account_id = created.json()["id"]

    resp = client.put(f"/api/accounts/{account_id}", json={"type": "checking"})
    assert 400 <= resp.status_code < 500, resp.text
    assert "cannot be changed" in resp.text
    assert AccountStore().find_by_id(account_id)["type"] == "cash"

    currency = client.put(f"/api/accounts/{account_id}", json={"currency": "USD"})
    assert 400 <= currency.status_code < 500, currency.text
    assert "currency" in currency.text and "cannot be changed" in currency.text
    assert AccountStore().find_by_id(account_id)["currency"] == "SGD"
