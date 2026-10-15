"""
Supplementary AI-tool tests for backend/ai/tools/equity.py — targeting coverage
gaps not yet exercised by tests/test_equity_tools.py (which sits at ~60%).

Covers:
  - _match_account_id: exact-ambiguous branch (lines 51-52), substr-unique branch (55)
  - create_grant tool: account_name resolved → success; account_name resolution error (261-265)
  - update_grant tool: account_name resolution error (392-393)
  - propose_vest: FMV prefill success path where price history has data (492-497)
  - record_vest: OpenNetWorthError → JSON error (612-613)
  - propose_vest_undo: deferred grant (no txn_id branch, line 661)
  - undo_vest: OpenNetWorthError → JSON error (709-710)
  - propose_8b_import: both-ref-and-sid error; neither error; mock preview success path
  - import_8b: payload not dict error; mock commit success; EightBImportError path
  - _resolve_pdf_bytes: all branches (both, attachment_ref cache miss, statement_id
    not found, statement_id no file_path, statement_id file missing, neither provided)

All tests are offline/isolated — no LLM, no network, no live data.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.ai.tools import registry as tool_registry
from backend.db.account_store import AccountStore
from backend.db.grant_store import GrantStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.price_history_store import PriceHistoryStore
from backend.db.transaction_store import TransactionStore
from backend.db.vest_event_store import VestEventStore
from backend.services import equity as equity_svc
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service

# ── Helper ──────────────────────────────────────────────────────────────────────


# A gated tool binds its confirm card to the tool_use_id of the call being gated, which a
# real turn always supplies to dispatch. Passing one here is that turn-loop binding.
_TOOL_USE_ID = "toolu_test"


def call(tool_name, **inp):
    text, proposed = asyncio.run(execute_tool(tool_name, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


def _preview(monkeypatch):
    """propose_vest(_undo)/propose_8b_import are now ALIASES of record_vest(_undo)/
    import_8b (the 2026-07-19 propose_*/apply_* merge) — the SAME tool previews or
    writes depending on should_preview_write(), not on which name it was called by. Call
    this in a test that wants the PREVIEW branch (a bare test call sets no cost surface,
    which fails OPEN to a direct write)."""
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)


# ── Fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Redirect every backing file to tmp_path + identity FX (USD base). PriceHistoryStore
    resolves against the per-test isolated database (db_isolated)."""
    # Rebind module-level store handles on the service.
    monkeypatch.setattr(equity_svc, "grants", GrantStore())
    monkeypatch.setattr(equity_svc, "vest_events", VestEventStore())
    monkeypatch.setattr(equity_svc, "instruments", InstrumentStore())
    monkeypatch.setattr(equity_svc, "positions", PositionStore())
    monkeypatch.setattr(equity_svc, "price_history", PriceHistoryStore())
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


# ── _match_account_id branch coverage ────────────────────────────────────────────


def test_match_account_id_exact_ambiguous_returns_error(isolated):
    """Two accounts with the exact same name → exact-ambiguous branch (lines 51-52)."""
    # The AccountStore allows duplicate names, so create two with the same name.
    AccountStore().create(
        {"name": "My Broker", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    AccountStore().create(
        {"name": "My Broker", "kind": "asset", "type": "investment", "currency": "USD"}
    )

    # create_grant with account_name triggers _match_account_id → ambiguous.
    inst, _ = _seed()
    res, proposed = call(
        "create_grant",
        instrument_id=inst["id"],
        total_units=100,
        account_name="My Broker",
        tranches=[{"vest_date": "2026-01-01", "units": 100}],
    )
    assert proposed is None
    assert "error" in res
    assert "ambiguous" in res["error"]


def test_match_account_id_substr_unique_returns_id(isolated):
    """One account whose name contains the search string → substr unique branch (line 55)."""
    AccountStore().create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    inst, _ = _seed()
    # "Vantage" is a substring of "Vantage Brokerage" — only one match → resolves OK.
    res, proposed = call(
        "create_grant",
        instrument_id=inst["id"],
        total_units=100,
        account_name="Vantage",
        tranches=[{"vest_date": "2026-01-01", "units": 100}],
    )
    assert proposed is None
    # Should succeed (not an error), creating the grant under the matched account.
    assert res.get("ok") is True


# ── create_grant: account_name resolution paths ───────────────────────────────────


def test_create_grant_account_name_resolved_to_existing(isolated):
    """create_grant with account_name that uniquely matches an existing account."""
    inst, acct = _seed()
    # Change the account name to something distinct.
    # The _seed function already creates an account named "Broker".
    res, proposed = call(
        "create_grant",
        instrument_id=inst["id"],
        total_units=100,
        account_name="Broker",
        tranches=[{"vest_date": "2026-01-01", "units": 100}],
    )
    assert proposed is None
    assert res.get("ok") is True
    # Grant should be created under the "Broker" account.
    stored = GrantStore().find_by_id(res["grant_id"])
    assert stored["account_id"] == acct["id"]


def test_create_grant_account_name_no_match_returns_error(isolated):
    """create_grant with account_name that matches nothing → lines 261-265."""
    inst, _ = _seed()
    res, proposed = call(
        "create_grant",
        instrument_id=inst["id"],
        total_units=100,
        account_name="Nonexistent Broker",
        tranches=[{"vest_date": "2026-01-01", "units": 100}],
    )
    assert proposed is None
    assert "error" in res
    assert "no existing account" in res["error"]
    # Nothing written.
    assert GrantStore().all() == []


# ── update_grant: account_name resolution error ───────────────────────────────────


def test_update_grant_account_name_no_match_returns_error(isolated):
    """update_grant with account_name that matches nothing → lines 392-393."""
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])

    res, proposed = call(
        "update_grant",
        grant_id=gr["grant_id"],
        account_name="Phantom Bank",
    )
    assert proposed is None
    assert "error" in res
    assert "no existing account" in res["error"]
    # Grant unchanged.
    still = GrantStore().find_by_id(gr["grant_id"])
    assert still["account_id"] == acct["id"]


# ── propose_vest: FMV prefill success branch (lines 492-497) ─────────────────────


def test_propose_vest_fmv_prefill_success_when_price_history_available(isolated, monkeypatch):
    """When fmv_per_unit is omitted and price_history has data, prefill succeeds.

    The display.fmv_source should mention 'prefilled from price history'.
    """
    _preview(monkeypatch)
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    # Seed a price point on/before the tranche's vest_date so fmv_prefill returns a value.
    ph = PriceHistoryStore()
    ph.record("ONWAI", 75.0, day="2026-01-01", source="yahoo")

    res, proposed = call(
        "record_vest",
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        # fmv_per_unit intentionally omitted — should be prefilled from price history
    )
    assert res is None
    assert proposed is not None
    assert proposed["fmv_per_unit"] == pytest.approx(75.0)
    assert proposed["display"]["fmv_source"] is not None
    assert "prefilled from price history" in proposed["display"]["fmv_source"]
    # No write occurred.
    assert VestEventStore().all() == []


# ── record_vest: OpenNetWorthError → JSON error (lines 612-613) ────────────────────


def test_apply_vest_unknown_grant_returns_error(isolated):
    """record_vest with a non-existent grant_id → OpenNetWorthError → JSON error."""
    res, proposed = call(
        "record_vest",
        grant_id="ghost-grant-id",
        tranche_id="ghost-tranche-id",
        fmv_per_unit=50.0,
    )
    assert proposed is None
    assert res.get("ok") is False
    assert "error" in res


# ── propose_vest_undo: deferred grant branch (line 661) ──────────────────────────


def test_propose_vest_undo_card_names_no_transaction_when_the_vest_wrote_none(
    isolated, monkeypatch
):
    """A vest writes no ledger row (any grant, since 2026-09-22), so its undo card must not
    mention a transaction at all — only a vest recorded BEFORE that change carries a
    txn_id, and only then does will_reverse name a legacy income txn."""
    _preview(monkeypatch)
    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"], tax_event="deferred")
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

    # Confirm the event has txn_id=None (deferred → no income txn).
    ev = VestEventStore().find_by_id(vest_event_id)
    assert ev["txn_id"] is None

    res, proposed = call("undo_vest", vest_event_id=vest_event_id)
    assert res is None
    assert proposed is not None
    will_reverse = proposed["display"]["will_reverse"]
    assert not any("income txn" in line for line in will_reverse), will_reverse
    assert any("lot" in line for line in will_reverse)
    # Nothing was actually reversed.
    assert VestEventStore().find_by_id(vest_event_id) is not None


# ── undo_vest: OpenNetWorthError → JSON error (lines 709-710) ──────────────


def test_apply_vest_undo_unknown_event_returns_error(isolated):
    """undo_vest with a non-existent event → OpenNetWorthError → JSON error."""
    res, proposed = call("undo_vest", vest_event_id="ghost-event-id")
    assert proposed is None
    assert res.get("ok") is False
    assert "error" in res


# ── propose_8b_import: _resolve_pdf_bytes error branches ─────────────────────────


def test_propose_8b_import_both_ref_and_sid_returns_error(isolated):
    """Passing both attachment_ref and statement_id → mutual-exclusion error."""
    res, proposed = call(
        "import_8b",
        attachment_ref="some-ref",
        statement_id="some-sid",
    )
    assert proposed is None
    assert res.get("ok") is False
    assert "both" in res["error"].lower() or "either" in res["error"].lower()


def test_propose_8b_import_neither_ref_nor_sid_returns_error(isolated):
    """Passing neither attachment_ref nor statement_id → guidance error."""
    res, proposed = call("import_8b")
    assert proposed is None
    assert res.get("ok") is False
    assert "attachment_ref" in res["error"] or "statement_id" in res["error"]


def test_propose_8b_import_attachment_ref_cache_miss_returns_error(isolated, monkeypatch):
    """attachment_ref that's not in the upload cache → cache-miss error."""

    # Monkeypatch the upload cache to return None (cache miss).
    monkeypatch.setattr(
        "backend.services.attachments.get_attachment_import_source",
        lambda ref: None,
    )
    res, proposed = call("import_8b", attachment_ref="expired-ref")
    assert proposed is None
    assert res.get("ok") is False
    assert (
        "30" in res["error"]
        or "cache" in res["error"].lower()
        or "expired" in res["error"].lower()
        or "statement_id" in res["error"]
    )


def test_propose_8b_import_mock_preview_success(isolated, monkeypatch):
    """propose_8b_import happy path: mock upload cache + mock preview_8b_import."""
    _preview(monkeypatch)
    fake_pdf = b"%PDF-fake"
    fake_preview = {
        "ok": True,
        "raw_count": 2,
        "rows_extracted": 2,
        "rows_dropped": 0,
        "proposed_grants": [
            {"instrument_id": "inst-1", "grant_date": "2023-01-01", "total_units": 200}
        ],
        "proposed_vests": [
            {
                "row_index": 0,
                "grant_id": "g1",
                "vest_date": "2024-01-01",
                "units": 100,
                "fmv_per_unit": 50.0,
            }
        ],
        "unresolved_rows": [],
        "document_currency": "SGD",
        "file_sha256": "abc123",
    }
    monkeypatch.setattr(
        "backend.services.attachments.get_attachment_import_source",
        lambda ref: (fake_pdf, "application/pdf"),
    )
    monkeypatch.setattr(
        "backend.services.equity_import.preview_8b_import",
        lambda content, instrument_id=None, document_currency="SGD": fake_preview,
    )

    res, proposed = call("import_8b", attachment_ref="valid-ref")
    assert res is None, "a staged card pauses the tool_use — no result yet"
    assert proposed is not None
    assert proposed["type"] == "8b_import_commit"
    display = proposed["display"]
    assert display["raw_count"] == 2
    assert display["rows_extracted"] == 2
    assert "1 new grant(s) proposed" in display["summary"]
    assert "1 vest event(s) proposed" in display["summary"]


def test_propose_8b_import_preview_raises_error(isolated, monkeypatch):
    """propose_8b_import when preview_8b_import raises EightBImportError."""
    from backend.services.equity_import import EightBImportError

    fake_pdf = b"%PDF-fake"
    monkeypatch.setattr(
        "backend.services.attachments.get_attachment_import_source",
        lambda ref: (fake_pdf, "application/pdf"),
    )
    monkeypatch.setattr(
        "backend.services.equity_import.preview_8b_import",
        lambda content, instrument_id=None, document_currency="SGD": (_ for _ in ()).throw(
            EightBImportError("invalid document format")
        ),
    )

    res, proposed = call("import_8b", attachment_ref="bad-pdf-ref")
    assert proposed is None
    assert res.get("ok") is False
    assert "invalid document format" in res["error"]


# ── import_8b: all branches ─────────────────────────────────────────────────


def test_apply_8b_import_payload_not_dict_returns_error(isolated):
    """import_8b with payload that's not a dict → validation error.

    Since S2 this is caught at the DISPATCH boundary (registry type validation) rather
    than inside the handler, so the shape is the standard ``{"error": ...}`` with no
    ``ok`` key — the handler is never entered. The error still names the bad field.
    """
    res, proposed = call("import_8b", payload="not-a-dict")
    assert proposed is None
    assert res.get("ok") is not True
    assert "payload" in res["error"].lower()


def test_apply_8b_import_mock_commit_success(isolated, monkeypatch):
    """import_8b happy path: mock commit_8b_import returns expected shape."""
    fake_result = {
        "ok": True,
        "grants_created": 1,
        "vests_applied": 2,
        "vests_idempotent": 0,
        "errors": [],
    }
    monkeypatch.setattr(
        "backend.services.equity_import.commit_8b_import",
        lambda payload: fake_result,
    )
    res, proposed = call("import_8b", payload={"proposed_grants": [], "proposed_vests": []})
    assert proposed is None
    assert res["ok"] is True
    assert res["grants_created"] == 1
    assert res["vests_applied"] == 2


def test_apply_8b_import_eight_b_error_returns_json_error(isolated, monkeypatch):
    """import_8b when commit raises EightBImportError → JSON error."""
    from backend.services.equity_import import EightBImportError

    monkeypatch.setattr(
        "backend.services.equity_import.commit_8b_import",
        lambda payload: (_ for _ in ()).throw(EightBImportError("commit failed: duplicate file")),
    )
    res, proposed = call("import_8b", payload={"proposed_grants": [], "proposed_vests": []})
    assert proposed is None
    assert res.get("ok") is False
    assert "commit failed" in res["error"]


def test_apply_8b_import_open_net_worth_error_returns_json_error(isolated, monkeypatch):
    """import_8b when commit raises OpenNetWorthError → JSON error."""
    from backend.core.errors import ValidationError

    monkeypatch.setattr(
        "backend.services.equity_import.commit_8b_import",
        lambda payload: (_ for _ in ()).throw(ValidationError("bad data", field="instrument_id")),
    )
    res, proposed = call("import_8b", payload={"proposed_grants": [], "proposed_vests": []})
    assert proposed is None
    assert res.get("ok") is False
    assert "error" in res


# ── _resolve_pdf_bytes: statement_id branches ─────────────────────────────────────


def test_propose_8b_import_statement_id_not_found_returns_error(isolated, monkeypatch):
    """statement_id that doesn't exist in StatementStore → not-found error."""
    res, proposed = call("import_8b", statement_id="ghost-statement-id")
    assert proposed is None
    assert res.get("ok") is False
    assert "not found" in res["error"].lower() or "ghost" in res["error"]


def test_propose_8b_import_statement_id_no_file_path_returns_error(isolated, monkeypatch):
    """statement_id found but record has no file_path → re-upload error."""
    import uuid as _uuid

    from backend.db.statement_store import StatementStore

    ss = StatementStore()
    # Inject a record with no file_path directly — bypasses the real create() signature.
    sid = str(_uuid.uuid4())
    rec = ss.insert({"id": sid, "name": "appendix8b.pdf", "file_path": "", "file_hash": ""})

    res, proposed = call("import_8b", statement_id=rec["id"])
    assert proposed is None
    assert res.get("ok") is False
    assert "file path" in res["error"].lower() or "re-upload" in res["error"].lower()


def test_propose_8b_import_statement_id_file_missing_from_disk_returns_error(isolated, monkeypatch):
    """statement_id found, file_path set, but file is missing on disk → re-upload error."""
    import uuid as _uuid

    import backend.paths as paths_mod
    from backend.db.statement_store import StatementStore

    # Patch DATA_DIR at the module level where the tool imports it.
    monkeypatch.setattr(paths_mod, "DATA_DIR", isolated)
    ss = StatementStore()
    sid = str(_uuid.uuid4())
    rec = ss.insert(
        {
            "id": sid,
            "name": "appendix8b.pdf",
            "file_path": "nonexistent/appendix8b.pdf",
            "file_hash": "",
        }
    )

    res, proposed = call("import_8b", statement_id=rec["id"])
    assert proposed is None
    assert res.get("ok") is False
    assert "missing" in res["error"].lower() or "re-upload" in res["error"].lower()


def test_propose_8b_import_statement_id_reads_file_and_calls_preview(isolated, monkeypatch):
    """statement_id → reads bytes from disk → calls preview_8b_import."""
    _preview(monkeypatch)
    import uuid as _uuid

    import backend.paths as paths_mod
    from backend.db.statement_store import StatementStore

    # Write a fake PDF to disk.
    pdf_dir = isolated / "uploads"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = pdf_dir / "test8b.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake content")

    monkeypatch.setattr(paths_mod, "DATA_DIR", isolated)
    ss = StatementStore()
    sid = str(_uuid.uuid4())
    rec = ss.insert(
        {
            "id": sid,
            "name": "appendix8b.pdf",
            "file_path": "uploads/test8b.pdf",
            "file_hash": "",
        }
    )

    fake_preview = {
        "ok": True,
        "raw_count": 0,
        "rows_extracted": 0,
        "rows_dropped": 0,
        "proposed_grants": [],
        "proposed_vests": [],
        "unresolved_rows": [],
        "document_currency": "SGD",
        "file_sha256": "deadbeef",
    }
    monkeypatch.setattr(
        "backend.services.equity_import.preview_8b_import",
        lambda content, instrument_id=None, document_currency="SGD": fake_preview,
    )

    res, proposed = call("import_8b", statement_id=rec["id"])
    # With zero rows, the summary should still be formed; proposed is returned.
    assert res is None, "a staged card pauses the tool_use — no result yet"
    assert proposed is not None


# ── propose_vest_undo: symbol lookup via grant + instrument (line 651) ────────────


def test_propose_vest_undo_display_includes_symbol(isolated, monkeypatch):
    """propose_vest_undo must include symbol in the display when grant+instrument exist."""
    _preview(monkeypatch)
    inst, acct = _seed()  # symbol = "ONWAI"
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

    res, proposed = call("undo_vest", vest_event_id=vest_event_id)
    assert res is None
    assert proposed["display"]["symbol"] == "ONWAI"


# ── get_equity_report: year parameter passed through ─────────────────────────────


def test_get_equity_report_year_parameter(isolated):
    """get_equity_report with explicit year returns data for that year."""
    res, proposed = call("get_equity_report", year=2025)
    assert proposed is None
    assert res["year"] == 2025
    assert "events" in res


# ── get_upcoming_vests: window_days filters correctly ─────────────────────────────


def test_get_upcoming_vests_window_days_zero_returns_subset(isolated):
    """get_upcoming_vests with window_days=0 returns only tranches vesting today."""
    inst, acct = _seed()
    _create_grant(inst["id"], acct["id"])
    # All tranches are in 2026–2028, so window_days=0 (today only) returns empty.
    res, _ = call("get_upcoming_vests", window_days=0)
    assert res == {"items": []}


# ── create_grant: create_new_account=True bypasses resolution ─────────────────────


def test_create_grant_create_new_account_true_bypasses_resolution(isolated):
    """create_grant with create_new_account=True and account_name skips _match_account_id."""
    inst, _ = _seed()
    accounts_before = len(AccountStore().all())

    res, proposed = call(
        "create_grant",
        instrument_id=inst["id"],
        total_units=100,
        account_name="Brand New Custodian",
        create_new_account=True,
        tranches=[{"vest_date": "2026-01-01", "units": 100}],
    )
    assert proposed is None
    assert res.get("ok") is True
    # A new account should have been created by the service.
    assert len(AccountStore().all()) > accounts_before


# ── update_grant: OpenNetWorthError from the service (lines 392-393) ─────────────


def test_update_grant_invalid_grant_id_returns_json_error(isolated):
    """update_grant with a non-existent grant_id → OpenNetWorthError → JSON error.

    This exercises lines 392-393 (the except OpenNetWorthError catch in update_grant).
    """
    res, proposed = call("update_grant", grant_id="ghost-grant-99", note="irrelevant")
    assert proposed is None
    assert res.get("ok") is False
    assert "error" in res


# ── propose_vest: fmv_prefill raises OpenNetWorthError (lines 496-497) ───────────


def test_propose_vest_fmv_prefill_raises_open_net_worth_error_is_swallowed(isolated, monkeypatch):
    """When fmv_prefill raises OpenNetWorthError, it is swallowed and fmv_per_unit stays None.

    With no fmv_per_unit and no prefill, preview_vest will return an error.
    This exercises the except OpenNetWorthError: pass branch (lines 496-497).
    """
    from backend.core.errors import NotFoundError

    inst, acct = _seed()
    gr = _create_grant(inst["id"], acct["id"])
    grant = GrantStore().find_by_id(gr["grant_id"])
    tranche = grant["tranches"][0]

    # Force fmv_prefill to raise an OpenNetWorthError.
    monkeypatch.setattr(
        equity_svc,
        "fmv_prefill",
        lambda grant_id, tranche_id: (_ for _ in ()).throw(
            NotFoundError("price history not found", grant_id=grant_id)
        ),
    )

    res, proposed = call(
        "record_vest",
        grant_id=gr["grant_id"],
        tranche_id=tranche["id"],
        # fmv_per_unit intentionally omitted; fmv_prefill will raise
    )
    # The exception is swallowed; preview_vest gets fmv_per_unit=None → error from service.
    assert "error" in res
    assert proposed is None


# ── propose_8b_import: rows_dropped summary part (line 799) ──────────────────────


def test_propose_8b_import_summary_includes_rows_dropped(isolated, monkeypatch):
    """When preview returns rows_dropped > 0, summary includes that information."""
    _preview(monkeypatch)
    fake_pdf = b"%PDF-fake"
    fake_preview = {
        "ok": True,
        "raw_count": 5,
        "rows_extracted": 3,
        "rows_dropped": 2,
        "proposed_grants": [],
        "proposed_vests": [
            {"row_index": 0, "grant_id": "g1", "vest_date": "2024-01-01", "units": 100},
            {"row_index": 1, "grant_id": "g1", "vest_date": "2024-07-01", "units": 100},
            {"row_index": 2, "grant_id": "g1", "vest_date": "2025-01-01", "units": 100},
        ],
        "unresolved_rows": [],
        "document_currency": "SGD",
        "file_sha256": "abc456",
    }
    monkeypatch.setattr(
        "backend.services.attachments.get_attachment_import_source",
        lambda ref: (fake_pdf, "application/pdf"),
    )
    monkeypatch.setattr(
        "backend.services.equity_import.preview_8b_import",
        lambda content, instrument_id=None, document_currency="SGD": fake_preview,
    )

    res, proposed = call("import_8b", attachment_ref="valid-ref-2")
    assert res is None, "a staged card pauses the tool_use — no result yet"
    summary = proposed["display"]["summary"]
    assert "2 rows dropped" in summary


# ── propose_8b_import: unresolved_rows summary part (line 805) ───────────────────


def test_propose_8b_import_summary_includes_unresolved_rows(isolated, monkeypatch):
    """When preview returns unresolved_rows, summary mentions them."""
    _preview(monkeypatch)
    fake_pdf = b"%PDF-fake"
    fake_preview = {
        "ok": True,
        "raw_count": 3,
        "rows_extracted": 3,
        "rows_dropped": 0,
        "proposed_grants": [],
        "proposed_vests": [],
        "unresolved_rows": [
            {"row_index": 0, "company_name": "Unknown Corp"},
            {"row_index": 1, "company_name": "Mystery Inc"},
        ],
        "document_currency": "SGD",
        "file_sha256": "abc789",
    }
    monkeypatch.setattr(
        "backend.services.attachments.get_attachment_import_source",
        lambda ref: (fake_pdf, "application/pdf"),
    )
    monkeypatch.setattr(
        "backend.services.equity_import.preview_8b_import",
        lambda content, instrument_id=None, document_currency="SGD": fake_preview,
    )

    res, proposed = call("import_8b", attachment_ref="valid-ref-3")
    assert res is None, "a staged card pauses the tool_use — no result yet"
    summary = proposed["display"]["summary"]
    assert (
        "could not be matched" in summary or "unresolved" in summary.lower() or "2 row" in summary
    )


# ── _resolve_pdf_bytes: get_upload_import_source raises exception (lines 899-900) ──


def test_propose_8b_import_attachment_ref_exception_in_cache_returns_error(isolated, monkeypatch):
    """When get_upload_import_source raises an exception, it is caught as 'failed to read'."""
    monkeypatch.setattr(
        "backend.services.attachments.get_attachment_import_source",
        lambda ref: (_ for _ in ()).throw(RuntimeError("cache corrupted")),
    )

    res, proposed = call("import_8b", attachment_ref="bad-cache-ref")
    assert proposed is None
    assert res.get("ok") is False
    assert "failed to read attachment" in res["error"]


# ── _resolve_pdf_bytes: statement_id exception from StatementStore (lines 917-918) ─


def test_propose_8b_import_statement_id_store_exception_returns_error(isolated, monkeypatch):
    """When StatementStore.find_by_id raises, it is caught as 'failed to read statement'."""
    import backend.db.statement_store as ss_mod_inner

    # Make find_by_id raise an exception to hit lines 917-918.
    original_store_cls = ss_mod_inner.StatementStore

    class BrokenStatementStore(original_store_cls):
        def find_by_id(self, sid):
            raise RuntimeError("disk I/O error")

    monkeypatch.setattr(ss_mod_inner, "StatementStore", BrokenStatementStore)

    res, proposed = call("import_8b", statement_id="any-id")
    assert proposed is None
    assert res.get("ok") is False
    assert "failed to read statement" in res["error"]
