"""Every import format through the ONE decision pipeline (SI-65).

Pins the unification (owner directive 2026-07-17): the structured formats (OFX/QIF/MT940/
CAMT) parse deterministically into the canonical contract and ride ``decision_preview`` /
``decision_commit`` — native external refs (hard dedup), band-stamped flags, a ledger check
when a window exists, a filed statement record, and batch rollback — and a MANUAL CSV/XLSX
column mapping TEACHES the pipeline (saved as a format profile, SI-16) before the same
decision preview runs, so the hand-mapping user gets byte-identical review AND the next
import of that layout needs no mapping at all. Zero LLM anywhere in this file — the seam is
left unstubbed on the deterministic paths on purpose (an accidental model call would fail
loudly), and stubbed only for the PDF reconcile-gate cases.

Also pins the reconcile gate (batch-aware-dedup addendum): ``BatchContext.complete`` is
claimed only when the statement's own identity reconciles, or the format is a bank-complete
export with a declared period and a clean parse.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools.statements import rollback_import_batch
from backend.core import llm_seam
from backend.core.txn_sources import primary_ref, primary_source
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as ledger_service
from backend.services import matching
from backend.services.statements import decisions as svc


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated, fx_rates):
    import backend.db.statement_store as stmt_mod

    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    # Fix 3 (security review round 2, consolidate-derived-data-storage): every consumer of
    # the statements blob directory now reads through
    # backend.core.paths.statements_files_dir(), a bare accessor resolved at call time —
    # there is exactly ONE binding left to patch, not one per consumer module.
    import backend.core.paths as core_paths_mod

    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(llm_seam, "_complete", None)  # any LLM call on this path = loud failure
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    ledger_service.accounts.insert(
        {"id": "acc", "name": "Checking", "kind": "asset", "currency": "SGD"}
    )
    ledger_service.accounts.insert(
        {"id": "eur", "name": "EUR Wallet", "kind": "asset", "currency": "EUR"}
    )


_OFX = b"""OFXHEADER:100
<OFX>
<CURDEF>SGD
<BANKACCTFROM><ACCTID>ACC-1</ACCTID></BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260201<DTEND>20260228
<STMTTRN><DTPOSTED>20260202<TRNAMT>-4.50<FITID>F1<NAME>Coffee</STMTTRN>
<STMTTRN><DTPOSTED>20260203<TRNAMT>2500.00<FITID>F2<NAME>Paycheck</STMTTRN>
</BANKTRANLIST>
</OFX>
"""

_QIF = b"""!Type:Bank
D02/02/2026
T-4.50
PCoffee
LTransport
^
D02/03/2026
T2500.00
PPaycheck
^
"""

_MT940 = b""":20:STMT001
:25:DE1234567890
:28C:1/1
:60F:C260201EUR1000,00
:61:2602020202D4,50NTRFNONREF//ref1
:86:COFFEE SHOP BERLIN
:61:2602030203C2500,00NTRFNONREF//ref2
:86:SALARY FEBRUARY
:62F:C260228EUR3495,50
"""

_CAMT = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
 <BkToCstmrStmt><Stmt>
  <Acct><Id><IBAN>DE00123456780000000000</IBAN></Id></Acct>
  <FrToDt><FrDtTm>2026-02-01T00:00:00</FrDtTm><ToDtTm>2026-02-28T23:59:59</ToDtTm></FrToDt>
  <Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">1000.00</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
  <Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">3495.50</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
  <Ntry><Amt Ccy="EUR">4.50</Amt><CdtDbtInd>DBIT</CdtDbtInd>
   <BookgDt><Dt>2026-02-02</Dt></BookgDt>
   <NtryDtls><TxDtls><Refs><AcctSvcrRef>R1</AcctSvcrRef></Refs>
    <RmtInf><Ustrd>COFFEE BERLIN</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
  <Ntry><Amt Ccy="EUR">2500.00</Amt><CdtDbtInd>CRDT</CdtDbtInd>
   <BookgDt><Dt>2026-02-03</Dt></BookgDt>
   <NtryDtls><TxDtls><Refs><AcctSvcrRef>R2</AcctSvcrRef></Refs>
    <RmtInf><Ustrd>SALARY</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
 </Stmt></BkToCstmrStmt>
</Document>
"""

_CSV = b"""Date,Memo,Amount,Category
2026-02-02,Coffee,-4.50,Transport
2026-02-03,Paycheck,2500.00,
"""
_MAPPING = {"date": "Date", "description": "Memo", "amount": "Amount", "category": "Category"}


def _live_rows():
    return [t for t in ledger_service.transactions.all() if not t.get("deleted")]


def _preview_commit_roundtrip(content: bytes, fmt: str, account_id: str) -> tuple[dict, dict]:
    prev = svc.decision_preview(content, fmt, account_id, filename=f"stmt.{fmt}")
    out = svc.decision_commit(prev["statement_id"], account_id, [])
    return prev, out


# ── per-format e2e: decision preview → external refs · commit → record + rollback ────


@pytest.mark.parametrize(
    "content,fmt,account_id,expected_ids",
    [
        (_OFX, "ofx", "acc", {("ofx", "ACC-1:F1"), ("ofx", "ACC-1:F2")}),
        (_MT940, "mt940", "eur", None),  # content-hash ids — shape asserted by prefix below
        (
            _CAMT,
            "camt",
            "eur",
            {
                ("statement", "DE00123456780000000000:R1"),
                ("statement", "DE00123456780000000000:R2"),
            },
        ),
        (_QIF, "qif", "acc", None),
    ],
)
def test_format_rides_decision_pipeline_end_to_end(content, fmt, account_id, expected_ids):
    prev = svc.decision_preview(content, fmt, account_id, filename=f"stmt.{fmt}")
    # Decision payload present: cached statement record, uncapped rows, zero LLM.
    assert prev["statement_id"].startswith("stmt")
    assert prev["tier"] == "structured" and prev["llm_cost_usd"] == 0
    rows = prev["rows"]
    assert len(rows) == 2 and all(not r["skip"] for r in rows)
    ids = {(primary_source(r), primary_ref(r)) for r in rows}
    if expected_ids is not None:
        assert ids == expected_ids  # byte-identical to the legacy importers' native refs
    # ONE namespace for every statement format (RFC 2026-08-01 D2) — csv/xlsx/pdf/qif/mt940/camt
    # all mint `statement`. `ofx` alone keeps its own: a FITID is spec-mandated unique per
    # account, so it is genuinely authoritative and collapsing it would downgrade it for nothing.
    expected_ns = "ofx" if fmt == "ofx" else "statement"
    assert all(src == expected_ns for src, _ in ids)
    assert not any(r["duplicate"] for r in rows)

    out = svc.decision_commit(prev["statement_id"], account_id, [])
    assert out["created"] == 2 and out["duplicates"] == 0
    booked = _live_rows()
    assert {(primary_source(t), primary_ref(t)) for t in booked} == ids
    # ONE writer value for every format (RFC 2026-08-02): `source` says WHO wrote the row,
    # and a file import is `import` regardless of format. WHICH format is the
    # external_refs namespace, asserted byte-identically by `ids` above — which is
    # exactly the point: the format was never lost, it was stored twice and the copy in
    # `source` was the unreliable one ("csv_import" was stamped on xlsx).
    assert all(t["source"] == "import" for t in booked)
    assert all(t.get("batch_id") == out["batch_id"] for t in booked)

    import backend.db.statement_store as stmt_mod

    record = stmt_mod.StatementStore().find_by_id(prev["statement_id"])
    assert record["account_id"] == account_id
    assert out["batch_id"] in record["batch_ids"]

    # Re-import → every row L0-hard-dedups on its (source, ref); commit books nothing.
    prev2 = svc.decision_preview(content, fmt, account_id, filename=f"stmt.{fmt}")
    assert all(r["duplicate"] for r in prev2["rows"])
    out2 = svc.decision_commit(prev2["statement_id"], account_id, [])
    assert out2["created"] == 0 and out2["duplicates"] == 2
    assert len(_live_rows()) == 2

    # Rollback un-books the whole batch; the freed ids re-import cleanly afterwards.
    asyncio.run(rollback_import_batch({"batch_id": out["batch_id"], "confirm": True}))
    assert _live_rows() == []
    prev3 = svc.decision_preview(content, fmt, account_id, filename=f"stmt.{fmt}")
    assert not any(r["duplicate"] for r in prev3["rows"])


def test_mt940_reconciles_and_produces_ledger_check():
    prev = svc.decision_preview(_MT940, "mt940", "eur", filename="stmt.mt940")
    # :60F:/:62F: prove the statement against itself: opening 1000 − 4.50 + 2500 = 3495.50.
    assert prev["reconcile"]["status"] == "ok"
    assert prev["meta"]["period_start"] == "2026-02-01"
    assert prev["meta"]["period_end"] == "2026-02-28"
    # A declared window → the pre-commit ledger check runs (empty book → zero rows, not None).
    assert prev["ledger_check"] is not None


def test_camt_reconciles_from_opbd_clbd():
    prev = svc.decision_preview(_CAMT, "camt", "eur", filename="stmt.camt")
    assert prev["reconcile"]["status"] == "ok"
    assert prev["meta"]["opening_balance"] == 1000.0
    assert prev["meta"]["closing_balance"] == 3495.5
    assert prev["ledger_check"] is not None


def test_qif_source_category_resolves_to_stored_id():
    prev = svc.decision_preview(_QIF, "qif", "acc", filename="stmt.qif")
    out = svc.decision_commit(prev["statement_id"], "acc", [])
    assert out["created"] == 2
    coffee = next(t for t in _live_rows() if t["description"] == "Coffee")
    assert coffee["category"] == "cat_db5642ab0000"  # QIF `LTransport` → the stored opaque id
    paycheck = next(t for t in _live_rows() if t["description"] == "Paycheck")
    assert not paycheck.get("category")  # no L field → Uncategorized, never invented


# ── manual-mapping teach flow (SI-16 → decision entrance) ───────────────────────────


def test_manual_mapping_teaches_then_decision_previews_and_hard_dedups():
    # Preview WITH the user's mapping: taught as a format profile, parsed with ZERO LLM
    # (the seam is unstubbed — any model call would raise), full decision payload.
    prev = svc.decision_preview(_CSV, "csv", "acc", filename="bank.csv", mapping=_MAPPING)
    assert prev["statement_id"].startswith("stmt")
    assert prev["format_profile"] and prev["format_profile"]["origin"] == "manual"
    assert prev["llm_cost_usd"] == 0
    rows = prev["rows"]
    assert len(rows) == 2
    assert all(primary_source(r) == "statement" for r in rows)
    # The mapped Category column rides as the file's own declared category.
    assert rows[0]["enrich"] is not None

    from backend.db.statement_format_store import StatementFormatStore

    profiles = StatementFormatStore().all_profiles()
    assert len(profiles) == 1 and profiles[0]["origin"] == "manual"
    assert profiles[0]["schema"]["category_col"] is not None

    out = svc.decision_commit(prev["statement_id"], "acc", [])
    assert out["created"] == 2
    coffee = next(t for t in _live_rows() if t["description"] == "Coffee")
    assert coffee["category"] == "cat_db5642ab0000"  # mapped Category column, resolved

    # THE BOARD GAP (import commit was a client workaround): a re-import used to double-book
    # because the client bulk path never minted external refs. Now: hard L0 dedup.
    prev2 = svc.decision_preview(_CSV, "csv", "acc", filename="bank.csv", mapping=_MAPPING)
    assert all(r["duplicate"] for r in prev2["rows"])
    out2 = svc.decision_commit(prev2["statement_id"], "acc", [])
    assert out2["created"] == 0 and out2["duplicates"] == 2
    assert len(_live_rows()) == 2


def test_taught_layout_needs_no_mapping_on_next_import():
    svc.decision_preview(_CSV, "csv", "acc", filename="bank.csv", mapping=_MAPPING)
    # Same LAYOUT, different content, NO mapping — format memory hit, zero LLM.
    other = b"Date,Memo,Amount,Category\n2026-03-01,Groceries,-20.00,\n"
    prev = svc.decision_preview(other, "csv", "acc", filename="march.csv")
    assert prev["format_profile"] and prev["format_profile"]["origin"] == "manual"
    assert prev["llm_cost_usd"] == 0
    assert len(prev["rows"]) == 1 and prev["rows"][0]["amount"] == -20.0


def test_mapping_that_does_not_line_up_fails_loud():
    """A mapping naming a column the file lacks must refuse — loudly and actionably.

    Asserted `StatementParseError` until 2026-07-19. `decision_preview` now validates the
    mapping up front (`import_csv.validate_mapping`) instead of inferring the failure from a
    schema that silently dropped the bad field, so the refusal arrives EARLIER and as
    `CsvImportError`. Both are `ImportError_` subclasses and every caller
    (`ai/tools/imports.py`, `api/import_api.py`) catches `ImportError_`, so the surfaced
    behaviour — ok:false / 422 — is unchanged; only the subclass and the message moved.
    Widened to the base class, which is the contract that actually matters, and the message
    is asserted since being actionable is the point of failing loud.
    """
    from backend.services.import_common import ImportError_

    with pytest.raises(ImportError_) as exc:
        svc.decision_preview(
            _CSV,
            "csv",
            "acc",
            filename="bank.csv",
            mapping={"date": "Datum", "description": "Memo", "amount": "Amount"},
        )
    # Names the bad column and lists the real ones, rather than a generic "didn't line up".
    assert "Datum" in str(exc.value)
    assert "Date" in str(exc.value)


def test_identical_twin_rows_keep_disambiguated_ids_through_commit():
    # Regression (found by the unification round-trip): decision_commit rebuilt drafts and
    # dropped the `:1` twin suffix — both twins booked under ONE id, so every re-import
    # re-created the second twin.
    twins = b"Date,Memo,Amount\n2026-02-02,Coffee,-5.00\n2026-02-02,Coffee,-5.00\n"
    mapping = {"date": "Date", "description": "Memo", "amount": "Amount"}
    prev = svc.decision_preview(twins, "csv", "acc", filename="twins.csv", mapping=mapping)
    out = svc.decision_commit(prev["statement_id"], "acc", [])
    assert out["created"] == 2
    refs = sorted(primary_ref(t) for t in _live_rows())
    assert refs[1] == f"{refs[0]}:1"  # both twins survive, distinctly (suffix on the ref)
    assert all(primary_source(t) == "statement" for t in _live_rows())
    prev2 = svc.decision_preview(twins, "csv", "acc", filename="twins.csv", mapping=mapping)
    out2 = svc.decision_commit(prev2["statement_id"], "acc", [])
    assert out2["created"] == 0 and out2["duplicates"] == 2


# ── the reconcile gate: BatchContext.complete is EARNED, never assumed ───────────────


def _capture_context(monkeypatch) -> list:
    captured: list = []
    real = matching.match_batch

    def spy(rows, *, context, **kw):
        captured.append(context)
        return real(rows, context=context, **kw)

    monkeypatch.setattr(matching, "match_batch", spy)
    return captured


def _stub_pdf(monkeypatch, meta: dict):
    rows = [
        {
            "date": "2026-02-02",
            "amount": 4.5,
            "direction": "OUT",
            "description": "COFFEE",
            "confidence": 0.97,
        },
    ]

    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            return json.dumps(meta)
        return json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def _fake_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_gate_pdf_with_period_but_failed_reconcile_never_claims_complete(monkeypatch):
    captured = _capture_context(monkeypatch)
    _stub_pdf(
        monkeypatch,
        {
            "currency": "SGD",
            "sign_convention": "card",
            "period_start": "2026-02-01",
            "period_end": "2026-02-28",
            "opening_balance": 0.0,
            "closing_balance": 999.0,
        },
    )
    prev = svc.decision_preview(_fake_pdf("COFFEE 4.50"), "pdf", "acc", filename="feb.pdf")
    assert prev["reconcile"]["status"] == "off"
    assert captured and captured[0].window is not None
    assert captured[0].complete is False  # bands still compute; promotion can never fire


def test_gate_pdf_with_clean_reconcile_claims_complete(monkeypatch):
    captured = _capture_context(monkeypatch)
    _stub_pdf(
        monkeypatch,
        {
            "currency": "SGD",
            "sign_convention": "card",
            "period_start": "2026-02-01",
            "period_end": "2026-02-28",
            "opening_balance": 0.0,
            "closing_balance": 4.5,
        },
    )
    svc.decision_preview(_fake_pdf("COFFEE 4.50"), "pdf", "acc", filename="feb.pdf")
    assert captured and captured[0].complete is True


def test_gate_bank_complete_export_with_period_claims_complete_without_balances(monkeypatch):
    captured = _capture_context(monkeypatch)
    prev = svc.decision_preview(_OFX, "ofx", "acc", filename="stmt.ofx")
    # OFX declares no opening balance → the identity can't prove itself…
    assert prev["reconcile"]["status"] == "unknown"
    # …but a bank-complete machine export with a declared period still qualifies.
    assert captured and captured[0].complete is True


def test_gate_unknown_reconcile_and_no_period_stays_incomplete(monkeypatch):
    captured = _capture_context(monkeypatch)
    no_period = _OFX.replace(b"<DTSTART>20260201<DTEND>20260228", b"")
    prev = svc.decision_preview(no_period, "ofx", "acc", filename="stmt.ofx")
    assert prev["reconcile"]["status"] == "unknown"
    assert captured and captured[0].complete is False and captured[0].window is None


def test_gate_commit_rederives_the_same_claim_from_the_cached_record(monkeypatch):
    prev = svc.decision_preview(_MT940, "mt940", "eur", filename="stmt.mt940")
    captured = _capture_context(monkeypatch)
    svc.decision_commit(prev["statement_id"], "eur", [])
    assert captured and captured[0].complete is True  # cached reconcile "ok" → same claim
