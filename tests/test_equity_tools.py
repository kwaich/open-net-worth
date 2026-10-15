"""
AI-tool dispatch tests for equity-compensation tools (feat/equity-comp, Lane L3).

Pin the APPROVE-TIER propose→confirm→apply contract for vest events, mirroring
tests/test_lots_tools.py and tests/test_income_tools.py:

  - list_grants / get_equity_report / get_upcoming_vests: read paths, proposed=None.
  - propose_vest: pure preview, writes NOTHING, returns proposed_change with
    idempotency_key + display.
  - record_vest: four-store write via apply_equity_change choke point.
  - Re-apply with same idempotency_key: no-op.
  - propose_vest_undo: shows reversal plan, writes nothing.
  - undo_vest: full reversal through apply_equity_change.
  - Error paths return JSON {"error":...}, not raised exceptions.
    record_vest, undo_vest, create_grant, update_grant, cancel_grant absent.
  - All tools appear in TOOL_SCHEMAS.

Offline, isolated to tmp_path, no LLM/network — not an integration test.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.ai.tools import registry as tool_registry
from backend.db.account_store import AccountStore
from backend.db.grant_store import GrantStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.transaction_store import TransactionStore
from backend.db.vest_event_store import VestEventStore

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.
from backend.services import equity as equity_svc
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service

# propose_vest/propose_vest_undo are now ALIASES of record_vest/undo_vest (2026-07-19
# propose_*/apply_* merge) — deliberately absent from this set, since aliases never appear
# in TOOL_SCHEMAS (zero prompt cost, see test_equity_tools_in_tool_schemas).
_EQUITY_TOOLS = {
    "list_grants",
    "get_equity_report",
    "get_upcoming_vests",
    "create_grant",
    "update_grant",
    "cancel_grant",
    "record_vest",
    "undo_vest",
}

_HEADLESS_EQUITY = {
    "list_grants",
    "get_equity_report",
    "get_upcoming_vests",
    "propose_vest",
    "propose_vest_undo",
}
_NOT_HEADLESS_EQUITY = {
    "record_vest",
    "undo_vest",
    "create_grant",
    "update_grant",
    "cancel_grant",
}


# A gated tool binds its confirm card to the tool_use_id of the call being gated, which a
# real turn always supplies to dispatch. Passing one here is that turn-loop binding.
_TOOL_USE_ID = "toolu_test"


def call(tool_name, **inp):
    text, proposed = asyncio.run(execute_tool(tool_name, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Redirect every backing file to tmp_path + simple FX (USD base)."""
    # accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated (above) redirects
    # the process-wide DB singleton, so the fresh AccountStore()/TransactionStore() instances
    # wired below resolve against it with no per-instance path-sync needed.
    # Rebind module-level store handles on the service.
    monkeypatch.setattr(equity_svc, "grants", GrantStore())
    monkeypatch.setattr(equity_svc, "vest_events", VestEventStore())
    monkeypatch.setattr(equity_svc, "instruments", InstrumentStore())
    monkeypatch.setattr(equity_svc, "positions", PositionStore())
    monkeypatch.setattr(lots_service, "instruments", InstrumentStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    # Identity FX: USD base.
    monkeypatch.setattr(equity_svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(lots_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "USD")
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(
        equity_svc,
        "to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0), 2),
    )
    monkeypatch.setattr(
        "backend.services.ledger.to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0), 2),
    )
    return tmp_path


def _preview(monkeypatch):
    """propose_vest/propose_vest_undo are now ALIASES of record_vest/undo_vest (the
    2026-07-19 propose_*/apply_* merge) — the SAME tool previews or writes depending on
    should_preview_write(), not on which name it was called by. Call this in a test that
    wants the PREVIEW branch (a bare test call sets no cost surface, which fails OPEN to a
    direct write); leave it unpatched in a test that wants the direct-write branch, even
    when it calls the tool via its propose_* alias name."""
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)


def _seed(symbol="ONWAI", ccy="USD"):
    """Seed instrument + account and return (instrument, account)."""
    inst = InstrumentStore().create(
        {
            "symbol": symbol,
            "name": f"{symbol} Corp",
            "quote_currency": ccy,
            "price_source_id": symbol,
        }
    )
    acct = AccountStore().create(
        {"name": "Broker", "kind": "asset", "type": "investment", "currency": ccy}
    )
    return inst, acct


def _create_grant(inst_id, acct_id, total_units=300, tax_event="at_vest"):
    """Create a grant with three equal tranches and return the result dict."""
    return equity_svc.apply_equity_change(
        {
            "type": "grant_create",
            "instrument_id": inst_id,
            "total_units": total_units,
            "grant_date": "2025-01-01",
            "grant_type": "rsu",
            "tax_event": tax_event,
            "account_id": acct_id,
            "tranches": [
                {"vest_date": "2026-01-01", "units": total_units / 3, "kind": "time"},
                {"vest_date": "2027-01-01", "units": total_units / 3, "kind": "time"},
                {"vest_date": "2028-01-01", "units": total_units / 3, "kind": "time"},
            ],
        }
    )


# ── Schema / headless marker assertions ──────────────────────────────────────────────


def test_equity_tools_in_tool_schemas():

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _EQUITY_TOOLS <= names


def test_apply_8b_import_payload_is_a_bare_free_form_object():
    """``payload`` carries whatever ``propose_8b_import`` staged — a genuinely free-form
    dict, not a fixed set of properties. The literal schema (pre-migration) declared it as
    a BARE ``{"type": "object"}`` with no ``properties`` and no ``additionalProperties`` key
    at all; a naive ``Dict[str, Any]`` model field would instead emit
    ``additionalProperties: true`` (schema_from_model.py's "bare free-form dict" quirk
    class) — a real behavioral difference for a strict tool-calling validator, not just a
    cosmetic one. Pins that equity.py's local ``_bare_object_schema`` json_schema_extra
    keeps stripping it now that create_grant/update_grant/import_8b are derived."""
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "import_8b")["input_schema"]
    payload_schema = schema["properties"]["payload"]
    assert payload_schema["type"] == "object"
    assert "properties" not in payload_schema
    assert "additionalProperties" not in payload_schema


# ── Read tools ────────────────────────────────────────────────────────────────────────


def test_list_grants_empty(isolated):
    res, proposed = call("list_grants")
    assert proposed is None
    assert res == {"grants": []}


def test_list_grants_with_data(isolated):
    inst, acct = _seed()
    _create_grant(inst["id"], acct["id"])
    res, proposed = call("list_grants")
    assert proposed is None
    assert len(res["grants"]) == 1
    g = res["grants"][0]
    assert g["symbol"] == "ONWAI"
    assert g["unvested_units"] == 300.0
    assert g["vested_units"] == 0.0
    assert len(g["tranches"]) == 3


def test_get_equity_report_empty(isolated):
    res, proposed = call("get_equity_report", year=2026)
    assert proposed is None
    assert res["year"] == 2026
    assert res["events"] == []
    assert res["total_gain_base"] == 0.0


def test_get_upcoming_vests_empty(isolated):
    res, proposed = call("get_upcoming_vests")
    assert proposed is None
    assert res == {"items": []}


def test_get_upcoming_vests_with_grant(isolated):
    inst, acct = _seed()
    _create_grant(inst["id"], acct["id"])
    res, _ = call("get_upcoming_vests")
    assert len(res["items"]) == 3
    assert res["items"][0]["symbol"] == "ONWAI"


# ── propose_vest: pure preview, writes nothing ────────────────────────────────────────


def test_propose_vest_returns_proposal_and_writes_nothing(isolated, monkeypatch):
    _preview(monkeypatch)
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    res, proposed = call(
        "record_vest",
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        fmv_per_unit=50.0,
        fmv_currency="USD",
        vest_date="2026-01-01",
    )
    assert res is None, "a staged card pauses the tool_use — no result yet"
    assert proposed["tool_use_id"] and proposed["grant_tool"]
    assert proposed is not None
    assert proposed["type"] == "vest_apply"
    assert proposed["grant_id"] == gr["grant_id"]
    assert proposed["tranche_id"] == tranche["id"]
    assert proposed["fmv_per_unit"] == 50.0
    assert proposed.get("idempotency_key")

    # The display confirm card contains the expected fields.
    display = proposed["display"]
    assert display["symbol"] == "ONWAI"
    assert display["fmv_per_unit"] == 50.0
    assert display["gross_gain_native"] == 100 * 50.0  # 100 units × $50
    assert display["tax_event"] == "at_vest"
    assert display["what_will_post"]

    # Nothing was written — propose never executes the vest.
    assert VestEventStore().all() == []
    assert TransactionStore().all() == []
    assert LotStore().all() == []


def test_propose_vest_unknown_grant_returns_error(isolated):
    res, proposed = call(
        "record_vest",
        grant_id="ghost",
        tranche_id="any",
        fmv_per_unit=50.0,
    )
    assert "error" in res
    assert proposed is None


def test_propose_vest_already_vested_tranche_returns_error(isolated, monkeypatch):
    _preview(monkeypatch)
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    # Apply the vest first.
    equity_svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": gr["grant_id"],
            "tranche_id": tranche["id"],
            "fmv_per_unit": 50.0,
            "fmv_currency": "USD",
            "vest_date": "2026-01-01",
        }
    )

    # Propose on the same (now-vested) tranche should return an error.
    res, proposed = call(
        "record_vest",
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        fmv_per_unit=50.0,
    )
    assert "error" in res
    assert proposed is None


def test_propose_vest_fmv_prefill_when_omitted(isolated, monkeypatch):
    """When fmv_per_unit is omitted and prefill finds nothing, display fmv_source is None."""
    _preview(monkeypatch)
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    # No price history seeded — fmv_per_unit is None → preview_vest raises a ValidationError
    # (fmv_per_unit is required), so the tool should return an error, not a proposal.
    res, proposed = call(
        "record_vest",
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        # fmv_per_unit intentionally omitted, no price history
    )
    # Without prefill data AND without explicit fmv_per_unit, preview_vest returns an error.
    assert "error" in res
    assert proposed is None


def test_propose_vest_deferred_display(isolated, monkeypatch):
    """Deferred grant: tax_event rides through the card as information, and the card
    promises no ledger row — the same as an at_vest grant, since no vest books one."""
    _preview(monkeypatch)
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"], tax_event="deferred")
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    res, proposed = call(
        "record_vest",
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        fmv_per_unit=40.0,
        fmv_currency="USD",
        vest_date="2026-01-01",
    )
    assert res is None, "a staged card pauses the tool_use — no result yet"
    assert proposed["tool_use_id"] and proposed["grant_tool"]
    display = proposed["display"]
    assert display["tax_event"] == "deferred"
    assert "tax deferred" in display["tax_event_note"]
    # No line may promise a ledger row — the preview renders exactly what the write stores.
    assert not any("income txn:" in line for line in display["what_will_post"])
    assert any("no ledger transaction" in line for line in display["what_will_post"])
    assert VestEventStore().all() == []


# ── record_vest: the four-store write path ────────────────────────────────────────────


def test_apply_vest_creates_event_and_lot_and_decrements_locked(isolated):
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    res, proposed = call(
        "record_vest",
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        fmv_per_unit=50.0,
        fmv_currency="USD",
        vest_date="2026-01-01",
    )
    assert proposed is None
    assert res["ok"] is True
    vest_event_id = res["vest_event_id"]

    # Vest event recorded.
    ev = VestEventStore().find_by_id(vest_event_id)
    assert ev is not None
    assert ev["grant_id"] == gr["grant_id"]
    assert ev["tranche_id"] == tranche["id"]
    assert ev["units"] == 100.0
    assert ev["fmv_per_unit"] == 50.0

    # No ledger row, whatever the tax_event — a vest delivers shares, not cash
    # (ADR 2026-09-22-a-vest-books-no-income-transaction).
    assert ev["txn_id"] is None
    assert TransactionStore().all() == []
    assert ev["gross_gain_native"] == 100 * 50.0  # the fact survives on the event

    # FMV-basis lot created.
    assert ev["lot_id"] is not None
    lots = LotStore().all()
    assert any(lot["id"] == ev["lot_id"] for lot in lots)

    # Locked position decremented.
    locked = PositionStore().find_by_id(gr["locked_position_id"])
    assert locked["quantity"] == pytest.approx(200.0)  # 300 − 100

    # Tranche marked vested.
    updated_grant = GrantStore().find_by_id(gr["grant_id"])
    updated_tranche = next(t for t in updated_grant["tranches"] if t["id"] == tranche["id"])
    assert updated_tranche["vested"] is True


def test_apply_vest_records_units_withheld(isolated):
    """Capability gap closed: units_withheld (already a POST /api/equity/.../vest field,
    stored on the vest_event for the record — informational, no leg in v1) was READ by
    equity_service._vest_apply but offered by neither propose_vest nor record_vest. Both
    units acquired (the lot) and income booked stay at the FULL vest units regardless —
    it does not silently reduce either."""
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    res, proposed = call(
        "record_vest",
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        fmv_per_unit=50.0,
        fmv_currency="USD",
        vest_date="2026-01-01",
        units_withheld=28.0,
    )
    assert proposed is None
    assert res["ok"] is True
    ev = VestEventStore().find_by_id(res["vest_event_id"])
    assert ev["units_withheld"] == 28.0
    # Full 100 units still acquired/booked — withholding is informational only in v1.
    assert ev["units"] == 100.0
    lot = LotStore().find_by_id(ev["lot_id"])
    assert lot["original_qty"] == 100.0


def test_apply_vest_units_withheld_defaults_to_zero(isolated):
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    res, _ = call(
        "record_vest",
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        fmv_per_unit=50.0,
        fmv_currency="USD",
        vest_date="2026-01-01",
    )
    ev = VestEventStore().find_by_id(res["vest_event_id"])
    assert ev["units_withheld"] == 0.0


def test_propose_vest_surfaces_units_withheld_on_the_confirm_card(isolated, monkeypatch):
    _preview(monkeypatch)
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    res, proposed = call(
        "record_vest",
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        fmv_per_unit=50.0,
        fmv_currency="USD",
        vest_date="2026-01-01",
        units_withheld=15.0,
    )
    assert res is None, "a staged card pauses the tool_use — no result yet"
    assert proposed["tool_use_id"] and proposed["grant_tool"]
    assert proposed["units_withheld"] == 15.0
    assert proposed["display"]["units_withheld"] == 15.0

    # Confirming resolves the card's own tool_use_id, exactly as the server does — and the
    # STORED args are what commit, so the withheld figure the card showed is the one recorded
    # no matter what this call passes alongside it.
    apply_res, apply_proposed = call(
        "record_vest", confirm_token=proposed["tool_use_id"], units_withheld=999.0
    )
    assert apply_proposed is None
    ev = VestEventStore().find_by_id(apply_res["vest_event_id"])
    assert ev["units_withheld"] == 15.0


def test_apply_vest_idempotent_on_key(isolated):
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    args = dict(
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        fmv_per_unit=50.0,
        fmv_currency="USD",
        vest_date="2026-01-01",
        idempotency_key="idem-k1",
    )
    first, _ = call("record_vest", **args)
    second, _ = call("record_vest", **args)  # retry with same key

    assert second.get("idempotent") is True
    assert second["vest_event_id"] == first["vest_event_id"]
    # Still exactly one event, no txn, one lot.
    assert len(VestEventStore().all()) == 1
    assert TransactionStore().all() == []


def test_apply_vest_deferred_no_txn(isolated):
    """Deferred grant: vest creates a lot + decrements locked, NO income txn."""
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"], tax_event="deferred")
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    res, _ = call(
        "record_vest",
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        fmv_per_unit=50.0,
        fmv_currency="USD",
        vest_date="2026-01-01",
    )
    assert res["ok"] is True
    assert res["txn_id"] is None
    assert TransactionStore().all() == []
    ev = VestEventStore().find_by_id(res["vest_event_id"])
    assert ev["lot_id"] is not None


# ── propose_vest_undo: shows reversal plan, writes nothing ───────────────────────────


def test_propose_vest_undo_returns_proposal_and_writes_nothing(isolated, monkeypatch):
    _preview(monkeypatch)
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    # First vest the tranche.
    vest_res = equity_svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": gr["grant_id"],
            "tranche_id": tranche["id"],
            "fmv_per_unit": 50.0,
            "fmv_currency": "USD",
            "vest_date": "2026-01-01",
        }
    )
    vest_event_id = vest_res["vest_event_id"]
    count_before = len(VestEventStore().all())

    res, proposed = call("undo_vest", vest_event_id=vest_event_id)
    assert res is None, "a staged card pauses the tool_use — no result yet"
    assert proposed["tool_use_id"] and proposed["grant_tool"]
    assert proposed is not None
    assert proposed["type"] == "vest_undo"
    assert proposed["vest_event_id"] == vest_event_id
    # NOTE: the old propose_vest_undo minted an idempotency_key here, but
    # equity_service._vest_undo never read it — dead metadata, dropped by the 2026-07-19
    # propose_*/apply_* merge (ai/tools/equity.py's _build_vest_undo_change docstring).
    display = proposed["display"]
    assert display["units"] == 100.0
    assert display["will_reverse"]

    # Nothing was reversed — propose is read-only.
    assert len(VestEventStore().all()) == count_before


def test_propose_vest_undo_unknown_event_returns_error(isolated):
    res, proposed = call("undo_vest", vest_event_id="ghost")
    assert "error" in res
    assert proposed is None


# ── undo_vest: full reversal ────────────────────────────────────────────────────


def test_apply_vest_undo_reverses_all_artifacts(isolated):
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    vest_res = equity_svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": gr["grant_id"],
            "tranche_id": tranche["id"],
            "fmv_per_unit": 50.0,
            "fmv_currency": "USD",
            "vest_date": "2026-01-01",
        }
    )
    vest_event_id = vest_res["vest_event_id"]

    undo_res, proposed = call("undo_vest", vest_event_id=vest_event_id)
    assert proposed is None
    assert undo_res["ok"] is True

    # Vest event deleted.
    assert VestEventStore().find_by_id(vest_event_id) is None

    # There was no income txn to reverse — a vest books none.
    assert vest_res.get("txn_id") is None
    assert TransactionStore().all() == []

    # Lot deleted.
    lot_id = vest_res.get("lot_id")
    assert LotStore().find_by_id(lot_id) is None

    # Locked position re-incremented back to full 300.
    locked = PositionStore().find_by_id(gr["locked_position_id"])
    assert locked["quantity"] == pytest.approx(300.0)

    # Tranche flipped back to unvested.
    updated_grant = GrantStore().find_by_id(gr["grant_id"])
    updated_tranche = next(t for t in updated_grant["tranches"] if t["id"] == tranche["id"])
    assert updated_tranche["vested"] is False
    assert updated_tranche["vest_event_id"] is None


# ── Grant config write tools ──────────────────────────────────────────────────────────


def test_create_grant_tool_creates_locked_position(isolated):
    inst, acct = _seed()
    res, proposed = call(
        "create_grant",
        instrument_id=inst["id"],
        total_units=200,
        grant_date="2025-06-01",
        grant_type="rsu",
        tax_event="at_vest",
        account_id=acct["id"],
        tranches=[
            {"vest_date": "2026-06-01", "units": 100},
            {"vest_date": "2027-06-01", "units": 100},
        ],
    )
    assert proposed is None
    assert res["ok"] is True
    locked_id = res["locked_position_id"]
    pos = PositionStore().find_by_id(locked_id)
    assert pos is not None
    assert pos["vesting_state"] == "unvested"
    assert pos["quantity"] == pytest.approx(200.0)


def test_create_grant_tool_unknown_instrument_returns_error(isolated):
    res, proposed = call(
        "create_grant",
        instrument_id="ghost",
        total_units=100,
        tranches=[{"vest_date": "2026-01-01", "units": 100}],
    )
    assert proposed is None
    assert "error" in res


def test_create_grant_tool_tranche_sum_mismatch_returns_error(isolated):
    inst, acct = _seed()
    res, proposed = call(
        "create_grant",
        instrument_id=inst["id"],
        total_units=300,
        account_id=acct["id"],
        tranches=[{"vest_date": "2026-01-01", "units": 100}],  # sum = 100 ≠ 300
    )
    assert proposed is None
    assert "error" in res


def test_update_grant_tool(isolated):
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    res, proposed = call("update_grant", grant_id=gr["grant_id"], note="updated note")
    assert proposed is None
    assert res["ok"] is True
    updated = GrantStore().find_by_id(gr["grant_id"])
    assert updated["note"] == "updated note"


def test_cancel_grant_tool(isolated):
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    res, proposed = call("cancel_grant", grant_id=gr["grant_id"])
    assert proposed is None
    assert res["ok"] is True
    updated = GrantStore().find_by_id(gr["grant_id"])
    assert updated["status"] == "cancelled"


def test_cancel_grant_unknown_returns_error(isolated):
    res, proposed = call("cancel_grant", grant_id="ghost")
    assert proposed is None
    assert "error" in res


# ── grant_price_per_unit tool schema + passthrough ───────────────────────────────────


def test_create_grant_tool_schema_has_grant_price_field():
    """grant_price_per_unit must appear in the create_grant tool input schema."""
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "create_grant")
    props = schema["input_schema"]["properties"]
    assert "grant_price_per_unit" in props
    desc = props["grant_price_per_unit"]["description"]
    assert "NOT used for tax" in desc or "Informational only" in desc


def test_update_grant_tool_schema_has_grant_price_field():
    """grant_price_per_unit must appear in the update_grant tool input schema."""
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "update_grant")
    props = schema["input_schema"]["properties"]
    assert "grant_price_per_unit" in props


def test_create_grant_tool_persists_price(isolated):
    """create_grant tool passes grant_price_per_unit through to the service choke point."""
    inst, acct = _seed()
    res, _ = call(
        "create_grant",
        instrument_id=inst["id"],
        total_units=100,
        grant_date="2025-06-01",
        account_id=acct["id"],
        grant_price_per_unit=20.36,
        tranches=[{"vest_date": "2026-06-01", "units": 100}],
    )
    assert res["ok"] is True
    stored = GrantStore().find_by_id(res["grant_id"])
    import math

    assert stored.get("grant_price_per_unit") is not None
    assert math.isclose(stored["grant_price_per_unit"], 20.36, rel_tol=1e-6)


def test_update_grant_tool_sets_price(isolated):
    """update_grant tool passes grant_price_per_unit through to the service choke point."""
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    res, _ = call("update_grant", grant_id=gr["grant_id"], grant_price_per_unit=37.85)
    assert res["ok"] is True
    import math

    stored = GrantStore().find_by_id(gr["grant_id"])
    assert math.isclose(stored["grant_price_per_unit"], 37.85, rel_tol=1e-6)


# ── create_grant account requirement + update_grant account move ─────────────────


def test_create_grant_tool_without_account_returns_error(isolated):
    """create_grant tool must return an error when no account is provided (no silent default)."""
    inst, _ = _seed()
    res, proposed = call(
        "create_grant",
        instrument_id=inst["id"],
        total_units=100,
        tranches=[{"vest_date": "2026-01-01", "units": 100}],
        # no account_id, no account_name
    )
    assert proposed is None
    assert "error" in res
    assert "account" in res["error"].lower()


def test_create_grant_tool_schema_no_employer_equity_default(isolated):
    """create_grant schema must NOT mention 'Employer Equity' as a default."""
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "create_grant")
    desc = schema["description"]
    assert "Employer Equity" not in desc
    props = schema["input_schema"]["properties"]
    acct_name_desc = props.get("account_name", {}).get("description", "")
    assert "Employer Equity" not in acct_name_desc


def test_update_grant_tool_schema_has_account_fields():
    """update_grant schema must include account_id and account_name for custodian moves."""
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "update_grant")
    props = schema["input_schema"]["properties"]
    assert "account_id" in props
    assert "account_name" in props
    # Both descriptions should mention moving the position.
    assert "position" in props["account_id"]["description"].lower()
    assert "position" in props["account_name"]["description"].lower()


def test_update_grant_tool_moves_account(isolated):
    """update_grant tool must move both grant and locked position when account_id is passed."""
    inst, acct_a = _seed()
    acct_b = AccountStore().create(
        {"name": "Crestpoint", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    gr = _create_grant(inst["id"], acct_a["id"])
    locked_id = gr["locked_position_id"]

    res, proposed = call("update_grant", grant_id=gr["grant_id"], account_id=acct_b["id"])
    assert proposed is None
    assert res["ok"] is True

    updated_grant = GrantStore().find_by_id(gr["grant_id"])
    assert updated_grant["account_id"] == acct_b["id"]

    locked = PositionStore().find_by_id(locked_id)
    assert locked["account_id"] == acct_b["id"]


# ── account_name resolution (_match_account_id) ──────────────────────────────────────


def test_update_grant_account_name_resolves_to_existing(isolated):
    """update_grant with account_name matching a unique account resolves to its id."""
    inst, acct_a = _seed()
    acct_b = AccountStore().create(
        {"name": "Crestpoint Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    gr = _create_grant(inst["id"], acct_a["id"])
    locked_id = gr["locked_position_id"]
    accounts_before = len(AccountStore().all())

    res, proposed = call(
        "update_grant", grant_id=gr["grant_id"], account_name="Crestpoint Brokerage"
    )
    assert proposed is None
    assert res["ok"] is True

    # Grant and position moved to the resolved account.
    updated_grant = GrantStore().find_by_id(gr["grant_id"])
    assert updated_grant["account_id"] == acct_b["id"]
    locked = PositionStore().find_by_id(locked_id)
    assert locked["account_id"] == acct_b["id"]

    # No new account was created.
    assert len(AccountStore().all()) == accounts_before


def test_update_grant_account_name_no_match_returns_error(isolated):
    """update_grant with account_name that matches nothing returns the guidance error."""
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    accounts_before = len(AccountStore().all())

    res, proposed = call("update_grant", grant_id=gr["grant_id"], account_name="Nonexistent Bank")
    assert proposed is None
    assert "error" in res
    assert "no existing account" in res["error"]
    assert "account_id from list_accounts" in res["error"]

    # Nothing was written.
    updated_grant = GrantStore().find_by_id(gr["grant_id"])
    assert updated_grant["account_id"] == acct["id"]
    assert len(AccountStore().all()) == accounts_before


def test_update_grant_account_name_ambiguous_returns_error(isolated):
    """update_grant with account_name matching multiple accounts returns ambiguous error."""
    inst, acct_a = _seed()
    AccountStore().create(
        {"name": "Crestpoint Equity", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    AccountStore().create(
        {"name": "Crestpoint Cash", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    gr = _create_grant(inst["id"], acct_a["id"])

    # "Crestpoint" is a substring of both accounts — should be ambiguous.
    res, proposed = call("update_grant", grant_id=gr["grant_id"], account_name="Crestpoint")
    assert proposed is None
    assert "error" in res
    assert "ambiguous" in res["error"]


def test_update_grant_create_new_account_true_creates(isolated):
    """update_grant with create_new_account=true creates the account rather than resolving."""
    inst, acct_a = _seed()
    gr = _create_grant(inst["id"], acct_a["id"])
    accounts_before = len(AccountStore().all())

    res, proposed = call(
        "update_grant",
        grant_id=gr["grant_id"],
        account_name="Brand New Bank",
        create_new_account=True,
    )
    assert proposed is None
    assert res["ok"] is True

    # A new account was created.
    assert len(AccountStore().all()) == accounts_before + 1
    new_accts = [a for a in AccountStore().all() if a["name"] == "Brand New Bank"]
    assert len(new_accts) == 1


# ── Release tools (two-step vest — ADR 2026-07-31) ───────────────────────────────────


def _create_pending_grant(inst_id, acct_id, total_units=300, *, release_required=True):
    return equity_svc.apply_equity_change(
        {
            "type": "grant_create",
            "instrument_id": inst_id,
            "total_units": total_units,
            "grant_date": "2025-01-01",
            "grant_type": "rsu",
            "tax_event": "at_vest",
            "release_required": release_required,
            "account_id": acct_id,
            "tranches": [
                {"vest_date": "2026-01-01", "units": total_units / 3, "kind": "time"},
                {"vest_date": "2027-01-01", "units": total_units / 3, "kind": "time"},
                {"vest_date": "2028-01-01", "units": total_units / 3, "kind": "time"},
            ],
        }
    )


def test_release_tools_are_registered(isolated):
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {"record_release", "list_pending_releases"} <= names


def test_list_pending_releases_empty(isolated):
    out, proposed = call("list_pending_releases")
    assert out["items"] == []
    assert out["showing"] == 0 and out["total"] == 0
    assert proposed is None


def test_record_release_delivers_a_pending_vest(isolated):
    inst, acct = _seed()
    grant_id = _create_pending_grant(inst["id"], acct["id"])["grant_id"]
    tranche_id = GrantStore().find_by_id(grant_id)["tranches"][0]["id"]
    ev = equity_svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant_id,
            "tranche_id": tranche_id,
            "fmv_per_unit": 10.0,
            "fmv_currency": "USD",
            "expected_release_date": "2026-02-01",
        }
    )
    assert ev["lot_id"] is None

    listed, _ = call("list_pending_releases")
    assert listed["total"] == 1
    assert listed["items"][0]["vest_event_id"] == ev["vest_event_id"]

    out, _ = call("record_release", vest_event_id=ev["vest_event_id"], release_date="2026-02-03")
    assert out["ok"] is True
    assert out["released_on"] == "2026-02-03"
    assert out["lot_id"]
    assert LotStore().find_by_id(out["lot_id"])["acquired_at"] == "2026-01-01"

    assert call("list_pending_releases")[0]["items"] == []


def test_record_release_can_annotate_a_legacy_lotted_vest(isolated):
    inst, acct = _seed()
    grant_id = _create_pending_grant(inst["id"], acct["id"], release_required=False)["grant_id"]
    tranche_id = GrantStore().find_by_id(grant_id)["tranches"][0]["id"]
    vest = equity_svc.apply_equity_change(
        {
            "type": "vest_apply",
            "grant_id": grant_id,
            "tranche_id": tranche_id,
            "fmv_per_unit": 10.0,
            "fmv_currency": "USD",
        }
    )
    VestEventStore().update_event(
        vest["vest_event_id"],
        {"pending_release": None, "released_on": None},
    )
    lots_before = LotStore().all()

    listed, _ = call("list_pending_releases")
    assert listed["items"][0]["release_mode"] == "record_only"

    out, _ = call(
        "record_release",
        vest_event_id=vest["vest_event_id"],
        release_date="2026-02-03",
    )

    assert out["ok"] is True
    assert out["release_mode"] == "record_only"
    assert VestEventStore().find_by_id(vest["vest_event_id"])["released_on"] == "2026-02-03"
    assert LotStore().all() == lots_before


def test_record_release_error_is_json_not_an_exception(isolated):
    out, _ = call("record_release", vest_event_id="nope")
    assert out["ok"] is False
    assert "not found" in out["error"].lower()
