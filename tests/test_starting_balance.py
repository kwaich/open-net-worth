"""An account's opening balance is ONE fact: ``account["opening_txn_id"]``.

The amount is the signed leg of the pointed-at "Starting Balance" row; the tracking start is
that row's date. Neither is stored on the account. These tests pin the write choke
(``set_account_opening``), the derived read side, the REST/AI wire contract (which did NOT
move — both opening fields are still input parameters and still derived response fields), and
the structural protections on the row.

RFC `.claude/rfcs/2026-07-31-opening-balance-is-one-fact.md`.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.core import txn_sources
from backend.core.errors import ConflictError, DataIntegrityError, NotFoundError, ValidationError
from backend.services import ledger as svc
from backend.services import tracking_start

# ── Isolation ──────────────────────────────────────────────────────────────────


@pytest.fixture
def iso(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(
        svc, "to_base", lambda amt, cur, base=None, rates=None: round(float(amt or 0), 2)
    )
    return tmp_path


@pytest.fixture
def client(iso):
    from backend.main import log_validation_error

    app = FastAPI()
    app.include_router(ledger_router)
    app.add_exception_handler(RequestValidationError, log_validation_error)
    return TestClient(app)


def _acct(**over):
    base = {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    base.update(over)
    return svc.accounts.create(base)


def _opening_txn(account_id):
    """The row the account's pointer resolves to — the ONE marker."""
    return svc._opening_row(svc.accounts.find_by_id(account_id))


def _balance(account_id) -> float:
    acct = svc.accounts.find_by_id(account_id)
    txns = svc.transactions.all()
    return svc.account_balance(acct, txns, svc.opening_date(acct, txns))


def _live(n: int | None = None):
    rows = [t for t in svc.transactions.all() if not t.get("deleted")]
    if n is not None:
        assert len(rows) == n, rows
    return rows


# ── core/txn_sources.py: "opening" is a DISPLAY value, never a stored source ─────


def test_opening_is_not_a_stored_source():
    """It was a `source` string; now it is the account's pointer. Nothing stores it."""
    assert "opening" not in txn_sources.KNOWN_SOURCES


def test_display_source_emits_opening_only_when_the_caller_says_so():
    """`core` is a leaf and cannot read an account, so the caller — which already holds
    `ledger.opening_txn_ids()` — supplies the bool."""
    assert txn_sources.display_source("manual", kind="adjustment") == "reconcile"
    disp = txn_sources.display_source("manual", kind="adjustment", is_opening=True)
    assert disp == "opening"
    assert txn_sources.added_by_label(disp) == "Starting Balance"


# ── services.ledger.set_account_opening — sign/leg, idempotency, sign flip ─────


def test_positive_opening_books_a_to_leg(iso):
    a = _acct()
    svc.set_account_opening(a["id"], 1000.0, "2026-01-01")
    txn = _opening_txn(a["id"])
    assert txn is not None
    assert txn["kind"] == "adjustment"
    assert txn["to_account_id"] == a["id"] and txn["to_amount"] == 1000.0
    assert txn["from_account_id"] is None
    assert txn["date"] == "2026-01-01"
    assert txn["cleared"] is True
    assert txn["category"] is None
    assert txn["description"] == "Starting Balance"
    # The row carries NO marker of its own — its identity is the account's pointer.
    assert txn["source"] == "manual"
    assert not txn.get("external_refs")
    assert svc.accounts.find_by_id(a["id"])["opening_txn_id"] == txn["id"]


def test_negative_opening_books_a_from_leg(iso):
    a = _acct(kind="liability", type="credit")
    svc.set_account_opening(a["id"], -608.88, "2026-05-31")
    txn = _opening_txn(a["id"])
    assert txn is not None
    assert txn["from_account_id"] == a["id"] and txn["from_amount"] == pytest.approx(608.88)
    assert txn["to_account_id"] is None


def test_zero_opening_mints_a_real_row_and_keeps_the_pointer(iso):
    """A zero opening is a REAL assertion — "my balance was zero when tracking started" —
    not an absence. It used to soft-delete the row and leave the cutoff standing behind
    nothing, which is how 8 live accounts ended up with a tracking start and no opening."""
    a = _acct()
    svc.set_account_opening(a["id"], 500.0, "2026-01-01")
    first_id = _opening_txn(a["id"])["id"]

    svc.set_account_opening(a["id"], 0.0, "2026-01-01")
    txn = _opening_txn(a["id"])
    assert txn is not None
    assert txn["id"] == first_id  # patched in place, never deleted-and-reminted
    assert txn["to_amount"] == 0.0
    assert txn["deleted"] is not True
    assert svc.accounts.find_by_id(a["id"])["opening_txn_id"] == first_id
    assert svc.opening_date(svc.accounts.find_by_id(a["id"])) == "2026-01-01"
    assert _balance(a["id"]) == 0.0
    _live(1)


def test_a_zero_leg_is_inert_in_the_fold(iso):
    a = _acct()
    svc.set_account_opening(a["id"], 0.0, "2026-01-01")
    svc.transactions.append(
        {
            "date": "2026-02-01",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 40.0,
            "from_currency": "SGD",
        }
    )
    assert _balance(a["id"]) == -40.0


def test_a_zero_row_stamps_base_currency_and_leaves_fx_rate_null(iso):
    """`0.0` is falsy — the stamp used to be gated on truthiness, so a zero row stored
    base_amount=0.0 with base_currency=None: the unstamped shape the historical-rate lock
    distrusts. A rate is undefined at zero, so fx_rate stays None (never 0.0)."""
    a = _acct()
    svc.set_account_opening(a["id"], 0.0, "2026-01-01")
    txn = _opening_txn(a["id"])
    assert txn["base_amount"] == 0.0
    assert txn["base_currency"] == "SGD"
    assert txn["fx_rate"] is None


def test_repeated_call_same_amount_is_idempotent_one_txn(iso):
    a = _acct()
    svc.set_account_opening(a["id"], 1000.0, "2026-01-01")
    svc.set_account_opening(a["id"], 1000.0, "2026-01-01")
    svc.set_account_opening(a["id"], 1000.0, "2026-01-01")
    _live(1)


def test_amount_change_patches_the_same_txn_id(iso):
    a = _acct()
    svc.set_account_opening(a["id"], 1000.0, "2026-01-01")
    first_id = _opening_txn(a["id"])["id"]
    svc.set_account_opening(a["id"], 1500.0, "2026-01-01")
    second = _opening_txn(a["id"])
    assert second["id"] == first_id
    assert second["to_amount"] == 1500.0
    _live(1)


def test_sign_flip_crossing_zero_swaps_leg_same_txn_id(iso):
    a = _acct()
    svc.set_account_opening(a["id"], 500.0, "2026-01-01")
    first_id = _opening_txn(a["id"])["id"]
    svc.set_account_opening(a["id"], -300.0, "2026-01-01")
    second = _opening_txn(a["id"])
    assert second["id"] == first_id
    assert second["to_account_id"] is None
    assert second["from_account_id"] == a["id"] and second["from_amount"] == 300.0
    _live(1)


def test_date_move_redates_the_same_txn_and_the_cutoff_moves_with_it(iso):
    a = _acct()
    svc.set_account_opening(a["id"], 1000.0, "2026-01-01")
    first_id = _opening_txn(a["id"])["id"]
    svc.set_account_opening(a["id"], 1000.0, "2025-11-15")
    second = _opening_txn(a["id"])
    assert second["id"] == first_id
    assert second["date"] == "2025-11-15"
    # The cutoff is READ OFF the row, so it cannot lag behind it.
    assert svc.opening_date(svc.accounts.find_by_id(a["id"])) == "2025-11-15"


def test_the_cutoff_and_the_row_date_cannot_drift(iso):
    """The old shape kept the cutoff in a second field and wrote it only when `as_of` was
    truthy, so an `as_of=None` call left a stale cutoff behind a re-dated row — and the
    strict `<` then made the opening exclude ITSELF and collapse the balance to 0. With one
    storage location the drift is unrepresentable, not merely guarded."""
    a = _acct()
    svc.set_account_opening(a["id"], 1000.0, "2026-03-01")
    svc.transactions.append(
        {
            "date": "2026-01-05",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 5.0,
            "from_currency": "SGD",
        }
    )
    svc.set_account_opening(a["id"], 1000.0, None)  # re-mints at the null-date fallback
    acct = svc.accounts.find_by_id(a["id"])
    row = _opening_txn(a["id"])
    assert svc.opening_date(acct) == row["date"] == "2026-01-05"
    assert _balance(a["id"]) == 995.0  # the opening counts at its own date, always


def test_unknown_account_raises_not_found(iso):
    with pytest.raises(NotFoundError):
        svc.set_account_opening("ghost", 100.0, "2026-01-01")


def test_opening_txn_is_always_cleared_and_reconcile_does_not_misfire(iso):
    # ADR 2026-07-09 invariant #2: cleared=True, or cleared_account_balance drops and
    # reconcile misfires.
    a = _acct(kind="liability", type="credit")
    svc.set_account_opening(a["id"], -608.88, "2026-05-31")
    txn = _opening_txn(a["id"])
    assert txn["cleared"] is True
    acct = svc.accounts.find_by_id(a["id"])
    txns = svc.transactions.all()
    cutoff = svc.opening_date(acct, txns)
    assert svc.cleared_account_balance(acct, txns, cutoff) == svc.account_balance(
        acct, txns, cutoff
    )
    # Asserting the SAME real balance must produce NO adjustment (no misfire).
    assert svc.reconcile(a["id"], -608.88, "2026-06-01") is None
    _live(1)


def test_uncleared_the_opening_row_is_refused(iso):
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    with pytest.raises(ConflictError):
        svc.set_transaction_cleared(_opening_txn(a["id"])["id"], False)


# ── Null-date fallback ───────────────────────────────────────────────────────────


def test_null_date_falls_back_to_earliest_own_txn(iso):
    a = _acct()
    for d, amt in (("2026-02-10", 20.0), ("2026-01-05", 5.0)):
        svc.transactions.append(
            {
                "date": d,
                "kind": "expense",
                "from_account_id": a["id"],
                "from_amount": amt,
                "from_currency": "SGD",
            }
        )
    svc.set_account_opening(a["id"], 1000.0, None)
    assert _opening_txn(a["id"])["date"] == "2026-01-05"


def test_null_date_no_txns_falls_back_to_today(iso):
    import datetime

    a = _acct()
    svc.set_account_opening(a["id"], 1000.0, None)
    assert _opening_txn(a["id"])["date"] == datetime.date.today().isoformat()


# ── The pointer: resolution, and what a dangling one does ───────────────────────


def test_no_pointer_means_no_opening_and_no_cutoff(iso):
    a = _acct()
    assert a["opening_txn_id"] is None
    assert svc.opening_amount(a) == 0.0
    assert svc.opening_date(a) is None


def test_a_dangling_pointer_raises_rather_than_silently_uncurtaining(iso):
    """Returning None would silently expose the account's pre-start history and MOVE its
    balance. A loud outage is fixable; a wrong net worth is not."""
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    svc.transactions.replace_all([])  # the row vanishes; the pointer does not
    acct = svc.accounts.find_by_id(a["id"])
    with pytest.raises(DataIntegrityError) as exc:
        svc.opening_date(acct)
    assert acct["id"] in str(exc.value)
    with pytest.raises(DataIntegrityError):
        svc.opening_amount(acct)


def test_a_soft_deleted_opening_row_is_also_a_dangling_pointer(iso):
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    svc.transactions.soft_delete(_opening_txn(a["id"])["id"])
    with pytest.raises(DataIntegrityError):
        svc.opening_date(svc.accounts.find_by_id(a["id"]))


def test_opening_txn_ids_covers_archived_accounts(iso):
    """A closed account still owns its opening row, and the row must stay protected."""
    a = _acct(archived=True)
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    assert _opening_txn(a["id"])["id"] in svc.opening_txn_ids()


def test_cutoffs_by_account_resolves_every_account_in_one_pass(iso):
    a = _acct(name="A")
    b = _acct(name="B")
    c = _acct(name="C")
    svc.set_account_opening(a["id"], 100.0, "2026-01-01")
    svc.set_account_opening(b["id"], 0.0, "2026-02-01")
    accts = svc.accounts.all()
    cutoffs = svc.cutoffs_by_account(accts, svc.transactions.all())
    assert cutoffs[a["id"]] == "2026-01-01"
    assert cutoffs[b["id"]] == "2026-02-01"
    assert cutoffs[c["id"]] is None


# ── opening_amount() — the derived read side ────────────────────────────────────


def test_opening_amount_zero_when_none(iso):
    a = _acct()
    assert svc.opening_amount(svc.accounts.find_by_id(a["id"])) == 0.0


def test_opening_amount_reflects_the_txn(iso):
    a = _acct(kind="liability", type="credit")
    svc.set_account_opening(a["id"], -250.5, "2026-01-01")
    assert svc.opening_amount(svc.accounts.find_by_id(a["id"])) == pytest.approx(-250.5)


def test_account_with_balance_derives_both_opening_fields(iso):
    """The wire contract did NOT move: both fields still ride the response, now derived
    off the row instead of read off the account."""
    a = _acct()
    svc.set_account_opening(a["id"], 777.0, "2026-01-01")
    hydrated = svc.account_with_balance(svc.accounts.find_by_id(a["id"]))
    assert hydrated["initial_balance"] == 777.0
    assert hydrated["initial_balance_date"] == "2026-01-01"
    assert hydrated["balance"] == 777.0


# ── Storage: the two legacy keys are GONE ───────────────────────────────────────


def test_account_store_round_trips_the_pointer_and_stores_neither_legacy_key(iso):
    a = _acct()
    assert "initial_balance" not in a
    assert "initial_balance_date" not in a
    assert a["opening_txn_id"] is None
    svc.set_account_opening(a["id"], 100.0, "2026-01-01")
    stored = svc.accounts.find_by_id(a["id"])
    assert "initial_balance" not in stored
    assert "initial_balance_date" not in stored
    assert stored["opening_txn_id"] == _opening_txn(a["id"])["id"]


def test_the_store_ignores_a_caller_supplied_legacy_key(iso):
    a = svc.accounts.create(
        {
            "name": "X",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 999.0,
            "initial_balance_date": "x",
        }
    )
    assert "initial_balance" not in a and "initial_balance_date" not in a


# ── REST: create/update route through the choke ─────────────────────────────────


def test_rest_add_account_mints_opening(client, iso):
    r = client.post(
        "/api/accounts",
        json={
            "name": "Lighthouse Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 5000.0,
            "initial_balance_date": "2026-01-01",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["initial_balance"] == 5000.0  # derived — the response shape is unchanged
    assert body["initial_balance_date"] == "2026-01-01"
    assert body["balance"] == 5000.0
    txn = _opening_txn(body["id"])
    assert txn is not None and txn["to_amount"] == 5000.0 and txn["date"] == "2026-01-01"
    assert svc.accounts.find_by_id(body["id"])["opening_txn_id"] == txn["id"]


def test_rest_add_account_requires_initial_balance(client, iso):
    r = client.post(
        "/api/accounts",
        json={"name": "Empty", "kind": "asset", "type": "cash", "currency": "SGD"},
    )
    assert r.status_code == 422, r.text
    assert "initial_balance" in r.text
    assert _live(0) == []


def test_rest_add_account_zero_opening_mints_a_zero_row_today(client, iso):
    r = client.post(
        "/api/accounts",
        json={
            "name": "Fresh",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0.0,
        },
    )
    body = r.json()
    assert body["initial_balance"] == 0.0
    assert body["initial_balance_date"] == date.today().isoformat()
    assert _opening_txn(body["id"])["to_amount"] == 0.0


def test_create_account_and_zero_opening_are_one_database_transaction(iso, monkeypatch):
    from backend.services import accounts as accounts_service

    original_update = svc.accounts.update_by_id
    rollback_calls: list[str] = []

    def fail_pointer(account_id, changes, *, conn=None):
        if "opening_txn_id" in changes:
            raise RuntimeError("pointer write failed")
        return original_update(account_id, changes, conn=conn)

    def record_compensating_rollback(account_id):
        rollback_calls.append(account_id)

    monkeypatch.setattr(svc.accounts, "update_by_id", fail_pointer)
    monkeypatch.setattr(
        accounts_service, "_rollback_created_account", record_compensating_rollback, raising=False
    )

    with pytest.raises(RuntimeError, match="pointer write failed"):
        accounts_service.create_account(
            {"name": "Atomic zero", "type": "cash", "currency": "SGD", "initial_balance": 0}
        )

    assert rollback_calls == []
    assert not any(a["name"] == "Atomic zero" for a in svc.accounts.all())
    assert not any(t.get("description") == "Starting Balance" for t in svc.transactions.all())


def test_rest_add_account_liability_negative_books_from_leg(client, iso):
    r = client.post(
        "/api/accounts",
        json={
            "name": "Visa",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": -608.88,
            "initial_balance_date": "2026-05-31",
        },
    )
    body = r.json()
    assert body["initial_balance"] == pytest.approx(-608.88)
    txn = _opening_txn(body["id"])
    assert txn["from_account_id"] == body["id"] and txn["from_amount"] == pytest.approx(608.88)


def test_rest_update_account_changes_opening_amount(client, iso):
    a = _acct()
    r = client.put(f"/api/accounts/{a['id']}", json={"initial_balance": 250.0})
    assert r.status_code == 200, r.text
    assert r.json()["initial_balance"] == 250.0
    assert _opening_txn(a["id"])["to_amount"] == 250.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_set_account_opening_refuses_non_finite_amounts(iso, value):
    a = _acct()

    with pytest.raises(ValidationError, match="finite"):
        svc.set_account_opening(a["id"], value)

    assert _opening_txn(a["id"]) is None


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_rest_update_account_refuses_non_finite_opening(client, iso, literal):
    a = _acct()
    r = client.put(
        f"/api/accounts/{a['id']}",
        content=f'{{"initial_balance":{literal}}}',
        headers={"content-type": "application/json"},
    )

    assert r.status_code == 422, r.text
    assert _opening_txn(a["id"]) is None


def test_rest_update_account_moves_opening_date_without_new_amount(client, iso):
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    first_id = _opening_txn(a["id"])["id"]
    r = client.put(f"/api/accounts/{a['id']}", json={"initial_balance_date": "2025-12-01"})
    assert r.status_code == 200, r.text
    txn = _opening_txn(a["id"])
    assert txn["id"] == first_id
    assert txn["date"] == "2025-12-01"
    assert txn["to_amount"] == 400.0  # amount untouched
    assert r.json()["initial_balance_date"] == "2025-12-01"


def test_rest_update_account_other_field_leaves_opening_untouched(client, iso):
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    first_id = _opening_txn(a["id"])["id"]
    r = client.put(f"/api/accounts/{a['id']}", json={"note": "hello"})
    assert r.status_code == 200, r.text
    txn = _opening_txn(a["id"])
    assert txn["id"] == first_id and txn["to_amount"] == 400.0 and txn["date"] == "2026-01-01"


def test_rest_update_account_sign_flip(client, iso):
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    r = client.put(f"/api/accounts/{a['id']}", json={"initial_balance": -100.0})
    assert r.status_code == 200, r.text
    assert r.json()["initial_balance"] == -100.0
    txn = _opening_txn(a["id"])
    assert txn["from_account_id"] == a["id"] and txn["from_amount"] == 100.0


def test_rest_cannot_set_the_server_owned_pointer(client, iso):
    """A caller-settable pointer would hand this account another account's row — i.e.
    another account's balance. It is not on the wire schema at all."""
    victim = _acct(name="Victim")
    svc.set_account_opening(victim["id"], 9999.0, "2026-01-01")
    stolen = svc.accounts.find_by_id(victim["id"])["opening_txn_id"]

    a = _acct(name="Thief")
    r = client.put(f"/api/accounts/{a['id']}", json={"opening_txn_id": stolen})
    assert r.status_code == 200, r.text
    assert svc.accounts.find_by_id(a["id"])["opening_txn_id"] is None
    assert r.json()["initial_balance"] == 0.0


# ── AI tools: create_account / update_accounts parity ──────────────────────────


@pytest.mark.asyncio
async def test_ai_add_account_tool_mints_opening(iso):
    from backend.ai.tools import accounts as acct_tools

    out, _ = await acct_tools.create_account(
        {"name": "Meridian Bank", "type": "cash", "currency": "SGD", "initial_balance": 12000}
    )
    body = json.loads(out)
    assert body["ok"] is True
    acct = body["account"]
    assert acct["initial_balance"] == 12000.0
    assert svc.accounts.find_by_id(acct["id"])["opening_txn_id"] == _opening_txn(acct["id"])["id"]


@pytest.mark.asyncio
async def test_ai_create_account_requires_initial_balance(iso):
    from backend.ai.tools import execute_tool

    out, _ = await execute_tool("create_account", {"name": "Fresh", "type": "cash"})
    body = json.loads(out)
    assert "initial_balance" in body["error"]


@pytest.mark.asyncio
async def test_ai_add_account_nonzero_still_anchors_today_by_default(iso):
    import datetime

    from backend.ai.tools import accounts as acct_tools

    out, _ = await acct_tools.create_account(
        {"name": "Savings", "type": "cash", "initial_balance": 500}
    )
    body = json.loads(out)
    assert body["account"]["initial_balance_date"] == datetime.date.today().isoformat()


@pytest.mark.asyncio
async def test_ai_update_accounts_tool_changes_opening(iso):
    from backend.ai.tools import accounts as acct_tools

    a = _acct()
    out, _ = await acct_tools.update_accounts({"account_id": a["id"], "initial_balance": 900})
    body = json.loads(out)
    assert body["ok"] is True
    assert body["account"]["initial_balance"] == 900.0
    assert _opening_txn(a["id"])["to_amount"] == 900.0


@pytest.mark.asyncio
async def test_ai_update_accounts_tool_moves_the_cutoff(iso):
    from backend.ai.tools import accounts as acct_tools

    a = _acct()
    svc.set_account_opening(a["id"], 300.0, "2026-01-01")
    out, _ = await acct_tools.update_accounts(
        {"account_id": a["id"], "initial_balance_date": "2025-10-01"}
    )
    body = json.loads(out)
    assert body["ok"] is True
    assert body["account"]["initial_balance_date"] == "2025-10-01"
    assert _opening_txn(a["id"])["date"] == "2025-10-01"


@pytest.mark.asyncio
async def test_ai_update_accounts_refuses_the_server_owned_pointer(iso):
    from backend.ai.tools import accounts as acct_tools

    a = _acct()
    out, _ = await acct_tools.update_accounts(
        {"account_id": a["id"], "opening_txn_id": "txn_somebody_elses"}
    )
    body = json.loads(out)
    assert body["ok"] is False
    assert "opening_txn_id" in body["error"]
    assert svc.accounts.find_by_id(a["id"])["opening_txn_id"] is None


@pytest.mark.asyncio
async def test_ai_list_accounts_projects_both_derived_opening_fields(iso):
    from backend.ai.tools import accounts as acct_tools

    a = _acct()
    svc.set_account_opening(a["id"], 333.0, "2026-01-01")
    out, _ = await acct_tools.list_accounts({})
    row = next(r for r in json.loads(out)["accounts"] if r["id"] == a["id"])
    assert row["initial_balance"] == 333.0
    assert row["initial_balance_date"] == "2026-01-01"


# ── Delete/edit protection ───────────────────────────────────────────────────────


def test_delete_opening_txn_is_refused(client, iso):
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    txn_id = _opening_txn(a["id"])["id"]
    r = client.delete(f"/api/transactions/{txn_id}")
    assert r.status_code == 409, r.text
    assert _opening_txn(a["id"]) is not None  # still there


def test_update_opening_txn_takes_the_narrow_edit_path(client, iso):
    """Editing the row IS the correction path (ADR 2026-07-28) — but only its amount and
    date, and only through ``set_account_opening``."""
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    txn_id = _opening_txn(a["id"])["id"]

    r = client.put(f"/api/transactions/{txn_id}", json={"to_amount": 650.0})
    assert r.status_code == 200, r.text
    assert svc.opening_amount(svc.accounts.find_by_id(a["id"])) == 650.0
    assert _opening_txn(a["id"])["id"] == txn_id  # same row, patched in place

    # Repurposing it into an ordinary transaction is still refused.
    assert client.put(f"/api/transactions/{txn_id}", json={"kind": "expense"}).status_code == 409


def test_editing_the_row_to_zero_is_now_allowed(client, iso):
    """Zero used to be implemented as a delete, so an edit to zero had to be refused.
    Zero is a legal opening now — the row stays, its id stays, the pointer stays."""
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    txn_id = _opening_txn(a["id"])["id"]
    r = client.put(f"/api/transactions/{txn_id}", json={"to_amount": 0.0})
    assert r.status_code == 200, r.text
    assert _opening_txn(a["id"])["id"] == txn_id
    assert svc.opening_amount(svc.accounts.find_by_id(a["id"])) == 0.0


def test_bulk_delete_by_ids_refuses_and_is_atomic(client, iso):
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    opening_id = _opening_txn(a["id"])["id"]
    other = svc.transactions.append(
        {
            "date": "2026-02-01",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    r = client.post("/api/transactions/bulk-delete", json={"ids": [other["id"], opening_id]})
    assert r.status_code == 409, r.text
    # atomic — the OTHER (non-protected) id must not have been deleted either
    assert svc.transactions.find_by_id(other["id"])["deleted"] is not True
    assert _opening_txn(a["id"]) is not None


def test_service_level_update_transaction_refuses(iso):
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    with pytest.raises(ConflictError):
        svc.update_transaction(_opening_txn(a["id"])["id"], {"note": "x"})


def test_service_level_bulk_delete_refuses(iso):
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    with pytest.raises(ConflictError):
        svc.bulk_delete_transactions([_opening_txn(a["id"])["id"]])


def test_filter_based_bulk_delete_spares_the_opening_row(iso):
    """The store owns no account knowledge, so the exemption arrives as ``protected_ids``
    from the service choke — the ONLY caller of ``bulk_delete_where``."""
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    other = svc.transactions.append(
        {
            "date": "2026-02-01",
            "kind": "adjustment",
            "to_account_id": a["id"],
            "to_amount": 7.0,
            "to_currency": "SGD",
        }
    )
    deleted = svc.delete_transactions_where(kind="adjustment", account_id=a["id"])
    assert deleted == 1
    assert _opening_txn(a["id"]) is not None
    assert svc.transactions.find_by_id(other["id"])["deleted"] is True


def test_filter_sweep_preview_and_delete_agree_on_the_exemption(iso):
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    preview = svc.preview_delete_transactions_where(kind="adjustment", account_id=a["id"])
    assert preview == []
    assert svc.delete_transactions_where(kind="adjustment", account_id=a["id"]) == 0


def test_an_opening_row_never_carries_an_batch_id(iso):
    """The one undelete path (statement-import rollback) works over ``query_by_batch``, so
    an opening row with no batch id is unreachable from it."""
    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    assert _opening_txn(a["id"]).get("batch_id") in (None, "")


@pytest.mark.asyncio
async def test_ai_delete_transactions_tool_refuses_opening(iso):
    """Regression (harness audit S1): the ``delete_transactions`` AI tool called
    ``TransactionStore.bulk_soft_delete`` directly, bypassing the ledger service's
    Starting Balance refusal AND its batched delete-audit write."""
    from backend.ai.tools.ledger import delete_transactions

    a = _acct()
    svc.set_account_opening(a["id"], 400.0, "2026-01-01")
    opening_id = _opening_txn(a["id"])["id"]
    other = svc.transactions.append(
        {
            "date": "2026-02-01",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    raw, _ = await delete_transactions({"transaction_ids": [other["id"], opening_id]})
    assert json.loads(raw)["ok"] is False
    # Atomic — the non-protected id in the same batch survives too.
    assert svc.transactions.find_by_id(other["id"])["deleted"] is not True
    assert _opening_txn(a["id"]) is not None


@pytest.mark.asyncio
async def test_ai_delete_transactions_tool_still_deletes_ordinary_rows(iso):
    """The reroute must not change the happy path's response shape."""
    from backend.ai.tools.ledger import delete_transactions

    a = _acct()
    other = svc.transactions.append(
        {
            "date": "2026-02-01",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    raw, _ = await delete_transactions({"transaction_ids": [other["id"], "nope"]})
    out = json.loads(raw)
    assert out["ok"] is True and out["deleted"] == 1
    assert out["deleted_ids"] == [other["id"]] and out["not_found"] == ["nope"]
    assert svc.transactions.find_by_id(other["id"])["deleted"] is True


# ── align_tracking_start: ONE write, the row itself ─────────────────────────────


def test_align_tracking_start_rewrites_the_opening_row(iso):
    a = _acct(kind="liability", type="credit")
    svc.set_account_opening(a["id"], -608.88, "2026-05-31")
    svc.transactions.append(
        {
            "date": "2026-04-05",
            "kind": "expense",
            "from_account_id": a["id"],
            "from_amount": 50.0,
            "from_currency": "SGD",
        }
    )
    before = _balance(a["id"])
    result = tracking_start.align_tracking_start(a["id"], "2026-04-05")
    assert result["balance_unchanged"] is True
    txn = _opening_txn(a["id"])
    assert txn["date"] == "2026-04-05"
    assert txn["from_amount"] == pytest.approx(558.88)  # 608.88 - 50
    # The cutoff moved because the ROW moved — there is no second field to keep in step.
    assert svc.opening_date(svc.accounts.find_by_id(a["id"])) == "2026-04-05"
    assert round(before, 2) == round(_balance(a["id"]), 2)
