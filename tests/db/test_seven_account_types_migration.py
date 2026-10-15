"""202609071200_seven_account_types: exhaustive remap, owned-asset conversion, NW invariant."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import pathlib
from datetime import date

import pytest
import sqlalchemy as sa
from alembic import command

from backend.db.alembic_config import make_config

_FROM = "202609061000_owned_assets"
_TARGET = "202609071200_seven_account_types"
_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "backend/db/alembic/versions" / f"{_TARGET}.py"
)


def _mod():
    spec = importlib.util.spec_from_file_location("_seven_types_rev", _PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize(
    "old,new",
    [
        ("checking", "cash"),
        ("savings", "cash"),
        ("cash", "cash"),
        ("ewallet", "cash"),
        ("debit_card", "cash"),
        ("money_market", "cash"),
        ("bank_transfer", "cash"),
        ("brokerage", "investment"),
        ("crypto_exchange", "investment"),
        ("custody", "investment"),
        ("pension", "retirement"),
        ("retirement_acct", "retirement"),
        ("govt_fund", "retirement"),
        ("loan_receivable", "receivable"),
        ("credit_card", "credit"),
        ("line_of_credit", "credit"),
        ("bnpl", "credit"),
        ("mortgage", "loan"),
        ("personal_loan", "loan"),
        ("auto_loan", "loan"),
        ("student_loan", "loan"),
        ("margin", "loan"),
        ("other_debt", "loan"),
    ],
)
def test_mapping_is_exhaustive(old, new):
    assert _mod().new_type(old, {}) == new


def test_locked_cash_becomes_fixed_deposit():
    assert _mod().new_type("savings", {"locked": True}) == "fixed_deposit"


def test_unknown_type_is_refused():
    with pytest.raises(RuntimeError):
        _mod().new_type("wat", {})


@pytest.mark.parametrize(
    "locked,expected",
    [
        (True, "fixed_deposit"),
        ("true", "fixed_deposit"),
        ("True", "fixed_deposit"),
        (1, "fixed_deposit"),
        ("false", "cash"),
        ("False", "cash"),
        ("no", "cash"),
        ("", "cash"),
        (False, "cash"),
        (None, "cash"),
    ],
)
def test_only_a_real_true_locks_a_cash_account_into_a_fixed_deposit(locked, expected):
    """`locked` was a free-form payload key, so a CSV/JSON import could write the STRING
    "false" — truthy in Python. A bare `payload.get("locked")` would have silently turned that
    savings account into a fixed deposit, taking its balance out of the runway baseline for
    good (the migration is one-way in practice)."""
    assert _mod().new_type("savings", {"locked": locked}) == expected


@pytest.fixture
def engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'onw.db'}"
    command.upgrade(make_config(url), _FROM)
    eng = sa.create_engine(url)
    yield eng, url
    eng.dispose()


def _acct(
    conn, *, id, name, kind, type_, currency="SGD", seq, payload_extra=None, opening_txn_id=None
):
    payload = {
        "id": id,
        "name": name,
        "kind": kind,
        "type": type_,
        "currency": currency,
        "archived": False,
        "opening_txn_id": opening_txn_id,
        **(payload_extra or {}),
    }
    conn.execute(
        sa.text(
            "INSERT INTO accounts (id, book_id, seq, name, kind, type, currency, archived, "
            "opening_txn_id, payload) "
            "VALUES (:id, 'default', :seq, :name, :kind, :type, :currency, 0, :otx, :payload)"
        ),
        {
            "id": id,
            "seq": seq,
            "name": name,
            "kind": kind,
            "type": type_,
            "currency": currency,
            "otx": opening_txn_id,
            "payload": json.dumps(payload),
        },
    )


def _txn(
    conn, *, id, seq, date, kind, frm=None, to=None, from_amount=None, to_amount=None, deleted=0
):
    payload = {
        "id": id,
        "date": date,
        "kind": kind,
        "from_account_id": frm,
        "to_account_id": to,
        "from_amount": from_amount,
        "to_amount": to_amount,
        "deleted": bool(deleted),
    }
    conn.execute(
        sa.text(
            "INSERT INTO transactions (id, book_id, seq, date, kind, from_account_id, "
            "to_account_id, from_amount, to_amount, deleted, source, description, payload) "
            "VALUES (:id, 'default', :seq, :date, :kind, :frm, :to, :fa, :ta, :deleted, "
            "'manual', :desc, :payload)"
        ),
        {
            "id": id,
            "seq": seq,
            "date": date,
            "kind": kind,
            "frm": frm,
            "to": to,
            "fa": None if from_amount is None else repr(float(from_amount)),
            "ta": None if to_amount is None else repr(float(to_amount)),
            "deleted": deleted,
            "desc": id,
            "payload": json.dumps(payload),
        },
    )


def test_property_account_becomes_owned_asset_and_kind_is_rederived(engine):
    eng, url = engine
    with eng.begin() as c:
        c.execute(sa.text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        _acct(
            c,
            id="acc_flat",
            name="Flat",
            kind="asset",
            type_="real_estate",
            seq=1,
            payload_extra={
                "asset_class": "property",
                "locked": True,
                "on_budget": False,
                "group": "property",
            },
            opening_txn_id="txn_open",
        )
        _acct(c, id="acc_loan", name="HDB loan", kind="liability", type_="mortgage", seq=2)
        _acct(c, id="acc_bank", name="DBS", kind="asset", type_="checking", seq=3)
        _txn(
            c,
            id="txn_open",
            seq=1,
            date="2024-01-01",
            kind="adjustment",
            to="acc_flat",
            to_amount=500000,
        )
        _txn(
            c,
            id="txn_up",
            seq=2,
            date="2025-06-01",
            kind="adjustment",
            to="acc_flat",
            to_amount=20000,
        )
        _txn(
            c,
            id="txn_old",
            seq=3,
            date="2023-12-31",
            kind="adjustment",
            to="acc_flat",
            to_amount=999,
        )
        _txn(
            c,
            id="txn_del",
            seq=4,
            date="2025-07-01",
            kind="adjustment",
            to="acc_flat",
            to_amount=999,
            deleted=1,
        )
    command.upgrade(make_config(url), _TARGET)
    with eng.connect() as c:
        rows = {
            r.id: r
            for r in c.execute(sa.text("SELECT id, kind, type, archived, payload FROM accounts"))
        }
        assert rows["acc_flat"].type == "receivable" and rows["acc_flat"].archived == 1
        assert rows["acc_loan"].type == "loan" and rows["acc_loan"].kind == "liability"
        assert rows["acc_bank"].type == "cash" and rows["acc_bank"].kind == "asset"
        p = json.loads(rows["acc_bank"].payload)
        assert not {"asset_class", "locked", "on_budget", "group"} & set(p)
        oa = [json.loads(r.payload) for r in c.execute(sa.text("SELECT payload FROM owned_assets"))]
        assert len(oa) == 1
        a = oa[0]
        assert a["name"] == "Flat" and a["category"] == "property" and a["currency"] == "SGD"
        # One mark per leg date, newest-first; pre-opening and deleted legs skipped.
        assert [(m["date"], m["value"]) for m in a["marks"]] == [
            ("2025-06-01", 520000.0),
            ("2024-01-01", 500000.0),
        ]
        assert a["cost"] == 500000.0 and a["cost_date"] == "2024-01-01"
        assert a["loan_account_id"] is None
        assert a["archived"] is False


def test_the_mark_ladder_values_every_historical_month(engine):
    """The converted account leaves the net-worth fold, so the owned asset must carry the same
    as-of ladder or every month before today drops by the property's whole value."""
    from backend.services.owned_assets_valuation import value_as_of

    eng, url = engine
    with eng.begin() as c:
        c.execute(sa.text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        _acct(
            c,
            id="acc_flat",
            name="Flat",
            kind="asset",
            type_="real_estate",
            seq=1,
            opening_txn_id="txn_open",
        )
        _txn(
            c,
            id="txn_open",
            seq=1,
            date="2024-01-01",
            kind="adjustment",
            to="acc_flat",
            to_amount=500000,
        )
        _txn(
            c,
            id="txn_up",
            seq=2,
            date="2025-06-01",
            kind="adjustment",
            to="acc_flat",
            to_amount=20000,
        )
    command.upgrade(make_config(url), _TARGET)
    with eng.connect() as c:
        asset = json.loads(c.execute(sa.text("SELECT payload FROM owned_assets")).scalar())
    assert value_as_of(asset, "2023-12-31") is None
    assert value_as_of(asset, "2024-06-01") == 500000.0
    assert value_as_of(asset, "2025-06-01") == 520000.0
    assert value_as_of(asset, None) == 520000.0


def test_an_archived_property_account_stays_archived_as_an_owned_asset(engine):
    """An archived account is already OUT of net worth; converting it live would add its
    balance back as a brand-new asset."""
    from backend.services.owned_assets_valuation import values_by_band_from_rows

    eng, url = engine
    with eng.begin() as c:
        c.execute(sa.text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        _acct(
            c,
            id="acc_sold",
            name="Old flat",
            kind="asset",
            type_="real_estate",
            seq=1,
            payload_extra={"archived": True},
        )
        c.execute(sa.text("UPDATE accounts SET archived = 1 WHERE id = 'acc_sold'"))
        _txn(
            c,
            id="txn_val",
            seq=1,
            date="2024-01-01",
            kind="adjustment",
            to="acc_sold",
            to_amount=300000,
        )
    command.upgrade(make_config(url), _TARGET)
    with eng.connect() as c:
        asset = json.loads(c.execute(sa.text("SELECT payload FROM owned_assets")).scalar())
    assert asset["archived"] is True
    assert values_by_band_from_rows([asset], "SGD") == {}


def test_a_non_property_asset_class_is_only_stripped_never_converted(engine):
    """`asset_class` was a free-text band override. Only "property" means "this is a thing you
    own"; anything else is a retired key on an ordinary account."""
    eng, url = engine
    with eng.begin() as c:
        c.execute(sa.text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        _acct(
            c,
            id="acc_gold",
            name="Bullion custody",
            kind="asset",
            type_="custody",
            seq=1,
            payload_extra={"asset_class": "commodity"},
        )
        _txn(
            c,
            id="txn_buy",
            seq=1,
            date="2024-01-01",
            kind="adjustment",
            to="acc_gold",
            to_amount=42000,
        )
    command.upgrade(make_config(url), _TARGET)
    with eng.connect() as c:
        row = c.execute(
            sa.text("SELECT type, kind, archived, payload FROM accounts WHERE id='acc_gold'")
        ).one()
        assert (row.type, row.kind, row.archived) == ("investment", "asset", 0)
        assert "asset_class" not in json.loads(row.payload)
        assert c.execute(sa.text("SELECT count(*) FROM owned_assets")).scalar() == 0


def test_the_promoted_columns_are_the_fallback_when_the_payload_omits_a_key(engine):
    """A live payload can lack a key its promoted column carries (db/tables.py's own note)."""
    eng, url = engine
    with eng.begin() as c:
        c.execute(sa.text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        _acct(c, id="acc_flat", name="Flat", kind="asset", type_="real_estate", seq=1)
        c.execute(
            sa.text("UPDATE accounts SET payload = :p WHERE id = 'acc_flat'"),
            {"p": json.dumps({"id": "acc_flat", "type": "real_estate"})},
        )
        _txn(
            c,
            id="txn_val",
            seq=1,
            date="2024-01-01",
            kind="adjustment",
            to="acc_flat",
            to_amount=1000,
        )
    command.upgrade(make_config(url), _TARGET)
    with eng.connect() as c:
        asset = json.loads(c.execute(sa.text("SELECT payload FROM owned_assets")).scalar())
    assert asset["currency"] == "SGD" and asset["name"] == "Flat"


def test_one_unknown_type_leaves_the_whole_book_untouched(engine):
    """The migration is fail-closed, and fail-closed means ALL-or-nothing: `new_type` raising on
    one row must leave every other row, the owned-assets table and `alembic_version` exactly as
    they were, so the next boot is refused on the same original database rather than half-
    migrated. The unit test above proves the RAISE; this proves nothing was written first —
    property conversion runs per row inside the same upgrade()."""
    eng, url = engine
    with eng.begin() as c:
        c.execute(sa.text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        _acct(c, id="acc_bank", name="DBS", kind="asset", type_="checking", seq=1)
        _acct(
            c,
            id="acc_flat",
            name="Flat",
            kind="asset",
            type_="real_estate",
            seq=2,
            payload_extra={"asset_class": "property"},
            opening_txn_id="txn_open",
        )
        _txn(
            c,
            id="txn_open",
            seq=1,
            date="2024-01-01",
            kind="adjustment",
            to="acc_flat",
            to_amount=500000,
        )
        _acct(c, id="acc_wat", name="Mystery", kind="asset", type_="wat", seq=3)

    def _snapshot():
        with eng.connect() as c:
            return (
                sorted(
                    tuple(r)
                    for r in c.execute(
                        sa.text("SELECT id, kind, type, archived, payload FROM accounts")
                    )
                ),
                c.execute(sa.text("SELECT COUNT(*) FROM owned_assets")).scalar(),
                c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar(),
            )

    before = _snapshot()
    assert before[1] == 0 and before[2] == _FROM

    with pytest.raises(Exception, match="wat"):
        command.upgrade(make_config(url), _TARGET)

    assert _snapshot() == before


def test_upgrade_is_idempotent_on_new_types(engine):
    eng, url = engine
    with eng.begin() as c:
        c.execute(sa.text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        _acct(c, id="acc_x", name="X", kind="asset", type_="cash", seq=1)
    command.upgrade(make_config(url), _TARGET)
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT type FROM accounts WHERE id='acc_x'")).scalar() == "cash"


@contextlib.contextmanager
def _services_on(url: str, tmp_path, monkeypatch):
    """Point the app's own engine + FX cache at ``url`` so the SERVICES (not raw SQL) are what
    reads the migrated book back."""
    from backend.core import config as _config
    from backend.db import engine as _eng
    from backend.services import fx_cache

    monkeypatch.setenv("ONW_DATABASE_URL", url)
    monkeypatch.setenv("ONW_QA_MODE", "1")
    monkeypatch.setattr(_config, "_settings", None)
    monkeypatch.setattr(fx_cache, "_FX_CACHE_FILE", tmp_path / "fx_cache.json")
    _eng.dispose_engine()
    try:
        yield
    finally:
        _eng.dispose_engine()


def test_demo_cast_reconciles_in_the_new_vocabulary(tmp_path, monkeypatch):
    from backend.scripts.demo import build as demo_build
    from backend.services import ledger

    url = f"sqlite:///{tmp_path / 'demo.db'}"
    with _services_on(url, tmp_path, monkeypatch):
        command.upgrade(make_config(url), "head")
        demo_build.build(today=date(2026, 8, 23), offline=True)
        nw = ledger.net_worth("USD")
    bd = nw["breakdown"]
    assert set(bd["account_classes"]) <= {
        "cash",
        "fixed_deposit",
        "settlement_cash",
        "retirement",
        "receivable",
        "property",
        "commodity",
        "other_asset",
    }
    assert bd["account_classes"].get("property", 0) > 0  # from the owned asset
    assert bd["owned_assets"] > 0
    assert set(bd["liability_classes"]) <= {"credit", "loan"}
    assert round(nw["net_worth"], 2) == round(
        bd["accounts_asset"]
        + bd["accounts_liability"]
        + bd["market_positions"]
        + bd["options_mark"],
        2,
    )


def test_net_worth_after_migration_equals_hand_computed_total(engine, tmp_path, monkeypatch):
    """The ADR's invariant, end to end: an OLD-vocabulary book migrated to head still totals
    exactly what its parts total — the property's value simply arrives via the owned asset
    instead of an account balance.

    No before/after comparison is possible (this checkout's stores refuse the old types
    outright), so the "before" is the hand-computed figure in the assertion."""
    import pytest as _pytest

    from backend.services import ledger

    eng, url = engine
    with eng.begin() as c:
        c.execute(sa.text("INSERT OR IGNORE INTO books (id) VALUES ('default')"))
        _acct(c, id="acc_chk", name="Checking", kind="asset", type_="checking", seq=1)
        _acct(c, id="acc_brk", name="Brokerage", kind="asset", type_="brokerage", seq=2)
        _acct(
            c,
            id="acc_flat",
            name="Flat",
            kind="asset",
            type_="real_estate",
            seq=3,
            payload_extra={"asset_class": "property"},
            opening_txn_id="t_flat",
        )
        _acct(c, id="acc_card", name="Card", kind="liability", type_="credit_card", seq=4)
        _acct(c, id="acc_mort", name="Mortgage", kind="liability", type_="mortgage", seq=5)
        _txn(
            c,
            id="t_chk",
            seq=1,
            date="2024-01-01",
            kind="adjustment",
            to="acc_chk",
            to_amount=37756.31,
        )
        _txn(
            c,
            id="t_brk",
            seq=2,
            date="2024-01-01",
            kind="adjustment",
            frm="acc_brk",
            from_amount=11269,
        )
        _txn(
            c,
            id="t_flat",
            seq=3,
            date="2024-01-01",
            kind="adjustment",
            to="acc_flat",
            to_amount=500000,
        )
        _txn(
            c,
            id="t_card",
            seq=4,
            date="2024-01-01",
            kind="adjustment",
            frm="acc_card",
            from_amount=2300,
        )
        _txn(
            c,
            id="t_mort",
            seq=5,
            date="2024-01-01",
            kind="adjustment",
            frm="acc_mort",
            from_amount=420000,
        )

    with _services_on(url, tmp_path, monkeypatch):
        command.upgrade(make_config(url), "head")
        nw = ledger.net_worth("SGD")

    bd = nw["breakdown"]
    assert nw["net_worth"] == _pytest.approx(37756.31 - 11269 + 500000 - 2300 - 420000)
    assert bd["owned_assets"] == 500000
    assert bd["account_classes"]["settlement_cash"] == -11269
    assert bd["account_classes"]["property"] == 500000
    assert bd["account_classes"]["cash"] == _pytest.approx(37756.31)
    assert bd["liability_classes"] == {"credit": -2300, "loan": -420000}
