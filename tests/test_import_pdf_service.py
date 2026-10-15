"""Unit tests for the PDF transaction import (services/import_pdf.py).

The real LLM parse is integration-only; here we STUB the ``core.llm_seam`` provider so the
test is deterministic + offline (no network). What's exercised: real ``pypdf`` text
extraction from a real PDF (built with pymupdf), the stubbed parse → the SAME two-leg draft
shape, the confidence/validity gate (low-confidence + bad rows skipped, never fabricated),
the content-hash external_ref dedup (re-import → ZERO duplicates), preview writing NOTHING,
and format detection (extension + %PDF content sniff). Stores isolated to tmp files (mirrors
test_import_mt940_service)."""

from __future__ import annotations

import json

import pytest

# ``_ai_parse_json`` is the real chatty-JSON extractor, imported at COLLECTION time on purpose:
# ``backend.ai.memory`` builds an ``asyncio.Lock()`` at module import (calling
# ``get_event_loop()``) — safe now (a default loop exists), but it raises if the import is
# deferred to mid-test after the loop is torn down. The regression tests reuse it to mirror the
# production parse wiring.
from backend.ai.memory import _parse_json as _ai_parse_json
from backend.core.txn_sources import primary_ref, primary_source
from backend.services import import_pdf as svc

# ── fixtures ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch):
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    return accounts, txns


@pytest.fixture
def checking(isolate_stores, fx_rates):
    accounts, _ = isolate_stores
    return accounts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "USD"})


def _make_pdf(text: str) -> bytes:
    """Build a real text PDF (pymupdf) so the test exercises actual pypdf extraction."""
    import fitz  # pymupdf — a declared dependency

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_SAMPLE_PDF = _make_pdf(
    "2026-01-02  Coffee Shop downtown  -4.50\n2026-01-03  Salary payment ACME  +2500.00"
)


def _stub_llm(monkeypatch, rows: list[dict]):
    """Register a deterministic ``complete`` on the LLM seam returning ``rows`` as JSON."""
    from backend.core import llm_seam

    async def _complete(prompt, **kwargs):
        return json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    monkeypatch.setattr(llm_seam, "_parse_json", None)  # falls back to json.loads


# A clean two-row parse the stub returns.
_GOOD_ROWS = [
    {
        "date": "2026-01-02",
        "amount": 4.50,
        "direction": "OUT",
        "description": "Coffee Shop",
        "confidence": 0.95,
    },
    {
        "date": "2026-01-03",
        "amount": 2500.0,
        "direction": "IN",
        "description": "Salary ACME",
        "confidence": 0.97,
    },
]


# ── text extraction ──────────────────────────────────────────────────────────────────


def test_extract_text_reads_pdf():
    text = svc._extract_text(_SAMPLE_PDF)
    assert "Coffee Shop" in text and "Salary" in text


def test_extract_empty_raises():
    with pytest.raises(svc.PdfImportError):
        svc._extract_text(b"")


def test_extract_non_pdf_raises():
    with pytest.raises(svc.PdfImportError):
        svc._extract_text(b"Date,Memo,Amount\n2026-01-01,x,1.0\n")


def test_extract_textless_pdf_raises():
    # A PDF with no inserted text → no selectable text → a clear, user-correctable error.
    blank = _make_pdf("")
    with pytest.raises(svc.PdfImportError):
        svc._extract_text(blank)


# ── parse + commit (stubbed LLM) ─────────────────────────────────────────────────────


def test_llm_failure_surfaces_error_not_silent_zero(checking, monkeypatch):
    # Regression: a FAILED parse (no AI provider / model error) used to return [] — the user
    # saw a misleading "0 transactions read" on a statement that clearly had transactions.
    # It must raise an actionable PdfImportError instead.
    from backend.core import llm_seam

    async def _boom(prompt, **kwargs):
        raise RuntimeError("no LLM provider registered on core.llm_seam")

    monkeypatch.setattr(llm_seam, "_complete", _boom)
    with pytest.raises(svc.PdfImportError) as ei:
        svc.preview_import(_SAMPLE_PDF, account_id=checking["id"])
    assert "provider" in str(ei.value).lower() or "AI" in str(ei.value)


def test_empty_llm_result_is_a_legitimate_zero(checking, monkeypatch):
    # The flip side: when the AI RUNS and genuinely finds nothing, that's a real empty preview
    # (0 rows), NOT an error — don't over-correct the failure case into raising on empty.
    _stub_llm(monkeypatch, [])
    res = svc.preview_import(_SAMPLE_PDF, account_id=checking["id"])
    assert res.total_rows == 0 and res.importable == 0


def test_commit_books_income_and_expense(checking, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS)
    res = svc.commit_import(_SAMPLE_PDF, account_id=checking["id"])
    assert res == {"created": 2, "duplicates": 0, "skipped": 0, "total_rows": 2}

    from backend.services import ledger as ledger_service

    txns = ledger_service.transactions.all()
    salary = next(t for t in txns if t["description"] == "Salary ACME")
    coffee = next(t for t in txns if t["description"] == "Coffee Shop")
    assert salary["kind"] == "income" and salary["to_amount"] == 2500.0
    assert salary["to_currency"] == "USD"
    assert coffee["kind"] == "expense" and coffee["from_amount"] == 4.50
    assert coffee["from_currency"] == "USD"


def test_external_ref_pdf_namespaced(checking, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS)
    svc.commit_import(_SAMPLE_PDF, account_id=checking["id"])
    from backend.services import ledger as ledger_service

    rows = ledger_service.transactions.all()
    assert all(primary_source(t) == "statement" for t in rows)
    refs = {primary_ref(t) for t in rows}
    assert len(refs) == 2  # distinct content hashes


def test_reimport_creates_zero_duplicates(checking, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS)
    first = svc.commit_import(_SAMPLE_PDF, account_id=checking["id"])
    assert first["created"] == 2
    second = svc.commit_import(_SAMPLE_PDF, account_id=checking["id"])
    assert second["created"] == 0
    assert second["duplicates"] == 2

    from backend.services import ledger as ledger_service

    assert len(ledger_service.transactions.all()) == 2


# ── the best-effort confidence / validity gate ───────────────────────────────────────


def test_low_confidence_row_skipped(checking, monkeypatch):
    rows = [
        {
            "date": "2026-01-02",
            "amount": 4.50,
            "direction": "OUT",
            "description": "Sure",
            "confidence": 0.9,
        },
        {
            "date": "2026-01-03",
            "amount": 9.99,
            "direction": "OUT",
            "description": "Unsure",
            "confidence": 0.2,
        },
    ]
    _stub_llm(monkeypatch, rows)
    res = svc.commit_import(_SAMPLE_PDF, account_id=checking["id"])
    assert res["created"] == 1
    assert res["skipped"] == 1


def test_bad_rows_skipped_not_fabricated(checking, monkeypatch):
    rows = [
        {"date": "", "amount": 4.50, "direction": "OUT", "description": "no date"},
        {"date": "2026-01-02", "amount": "n/a", "direction": "OUT", "description": "bad amount"},
        {"date": "2026-01-02", "amount": 5.0, "direction": "MAYBE", "description": "bad dir"},
        {"date": "2026-01-02", "amount": 0, "direction": "OUT", "description": "zero amount"},
        {"date": "2026-01-02", "amount": 7.0, "direction": "IN", "description": "good"},
    ]
    _stub_llm(monkeypatch, rows)
    # The good row's amount must exist in the source text (span-verification guard).
    res = svc.commit_import(_make_pdf("2026-01-02  good  7.00"), account_id=checking["id"])
    assert res["created"] == 1
    assert res["skipped"] == 4


def test_missing_confidence_field_passes(checking, monkeypatch):
    # A model that omits confidence is treated as "no signal" → the row is NOT dropped.
    rows = [{"date": "2026-01-02", "amount": 4.50, "direction": "OUT", "description": "Coffee"}]
    _stub_llm(monkeypatch, rows)
    res = svc.commit_import(_SAMPLE_PDF, account_id=checking["id"])
    assert res["created"] == 1


def test_llm_failure_raises_on_commit(checking, monkeypatch):
    # A provider error surfaces an actionable PdfImportError (the API maps it → 422), NOT a
    # silent {created:0} that reads as an empty statement. (Was: yielded zero rows silently.)
    from backend.core import llm_seam

    async def _boom(prompt, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_seam, "_complete", _boom)
    with pytest.raises(svc.PdfImportError):
        svc.commit_import(_SAMPLE_PDF, account_id=checking["id"])


# ── preview + shape ──────────────────────────────────────────────────────────────────


def test_preview_writes_nothing(checking, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS)
    from backend.services import ledger as ledger_service

    prev = svc.preview_import(_SAMPLE_PDF, account_id=checking["id"])
    assert prev.importable == 2
    assert prev.total_rows == 2
    assert len(prev.sample) == 2
    assert ledger_service.transactions.all() == []


def test_preview_sample_shape(checking, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS)
    prev = svc.preview_import(_SAMPLE_PDF, account_id=checking["id"])
    row = next(r for r in prev.sample if r["description"] == "Coffee Shop")
    assert set(row) == {
        "date",
        "payee",
        "description",
        "amount",
        "currency",
        "kind",
        "category",
        "original_amount",
        "original_currency",
        "reference",
        "external_refs",
        "flags",
    }
    assert row["amount"] == -4.50
    assert row["kind"] == "expense"
    assert row["currency"] == "USD"


def test_no_account_chosen_raises(isolate_stores, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS)
    with pytest.raises(svc.PdfImportError):
        svc.commit_import(_SAMPLE_PDF, account_id=None)


# ── robust parse of real-world LLM reply shapes (regression) ─────────────────────────


def _stub_llm_raw(monkeypatch, reply: str):
    """Register a ``complete`` returning ``reply`` verbatim, parsed by the REAL chatty-JSON
    extractor (``ai.memory._parse_json``) — mirrors the production wiring."""
    from backend.core import llm_seam

    async def _complete(prompt, **kwargs):
        return reply

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    monkeypatch.setattr(llm_seam, "_parse_json", _ai_parse_json)


_ONE_ROW_JSON = (
    '{"date": "2026-01-02", "amount": 4.50, "direction": "OUT", '
    '"description": "Coffee Shop", "confidence": 0.95}'
)


def test_object_wrapped_array_is_extracted(checking, monkeypatch):
    # Regression (Meridian Bank PDF "0 transactions read"): a reasoning model (grok-4.5) sometimes wraps
    # the array in an object — {"transactions": [...]}. The old code rejected any non-list and
    # reported a false empty statement. It must now unwrap the inner list.
    for key in ("transactions", "rows", "items", "data"):
        reply = '{"%s": [%s]}' % (key, _ONE_ROW_JSON)
        _stub_llm_raw(monkeypatch, reply)
        res = svc.preview_import(_SAMPLE_PDF, account_id=checking["id"])
        assert res.total_rows == 1, f"wrapper key {key!r} not unwrapped"
        assert res.importable == 1


def test_reasoning_prose_before_array_is_extracted(checking, monkeypatch):
    # Regression: the model emits reasoning containing a stray '{' brace BEFORE the final
    # array. The shared parser's fallback grabbed the wrong '{...}' span → None → 0 rows.
    reply = "Row one looks like a credit of {1000.00}.\nFinal answer:\n[%s]" % _ONE_ROW_JSON
    _stub_llm_raw(monkeypatch, reply)
    res = svc.preview_import(_SAMPLE_PDF, account_id=checking["id"])
    assert res.total_rows == 1
    assert res.importable == 1


def test_fenced_and_trailing_prose_array_is_extracted(checking, monkeypatch):
    reply = "```json\n[%s]\n```\nHope that helps!" % _ONE_ROW_JSON
    _stub_llm_raw(monkeypatch, reply)
    res = svc.preview_import(_SAMPLE_PDF, account_id=checking["id"])
    assert res.total_rows == 1


def test_single_object_row_is_wrapped(checking, monkeypatch):
    # A model that returns a bare object for a one-transaction statement (not an array).
    _stub_llm_raw(monkeypatch, _ONE_ROW_JSON)
    res = svc.preview_import(_SAMPLE_PDF, account_id=checking["id"])
    assert res.total_rows == 1


def test_genuinely_empty_reply_stays_zero(checking, monkeypatch):
    # Don't over-correct: an empty array / no-JSON reply is a legitimate 0, not an error.
    _stub_llm_raw(monkeypatch, "[]")
    assert svc.preview_import(_SAMPLE_PDF, account_id=checking["id"]).total_rows == 0
    _stub_llm_raw(monkeypatch, "I could not find any transactions.")
    assert svc.preview_import(_SAMPLE_PDF, account_id=checking["id"]).total_rows == 0


# ── enrichment: clean payee · foreign charge · reference · dedup id (E2E) ────────────


_RICH_ROW = {
    "date": "2026-01-02",
    "amount": 60.36,
    "direction": "OUT",
    "payee": "Lunara",
    "description": "MPOS*LUNARA CAFE DIST7",
    "original_amount": 845000.0,
    "original_currency": "VND",
    "reference": "10433218196001338908386",
    "confidence": 0.97,
}

# The span-verification guard (D4.5) checks extracted amounts/references exist in the source
# text — so the fixture PDF must actually SHOW what the stub claims was read from it.
_RICH_PDF = _make_pdf(
    "2026-01-02  MPOS*LUNARA CAFE DIST7  60.36\nRef No. : 10433218196001338908386\nVND 845,000.00"
)


def test_preview_surfaces_payee_foreign_charge_and_reference(checking, monkeypatch):
    _stub_llm(monkeypatch, [_RICH_ROW])
    prev = svc.preview_import(_RICH_PDF, account_id=checking["id"])
    row = prev.sample[0]
    assert row["payee"] == "Lunara"  # clean merchant name (primary label)
    assert row["description"] == "MPOS*LUNARA CAFE DIST7"  # raw kept as sub-line
    assert row["original_amount"] == 845000.0 and row["original_currency"] == "VND"
    assert row["reference"] == "10433218196001338908386"
    assert primary_source(row) == "statement"
    # ONE deterministic statement ref per row; the legacy "ref:"-prefixed twin is gone (b7f607da8).
    assert any(
        e.get("source") == "statement" and e.get("ref") and not e["ref"].startswith("ref:")
        for e in row["external_refs"]
    )


def test_commit_keeps_raw_description_clean_payee_foreign_charge_no_junk_keys(
    checking, monkeypatch
):
    _stub_llm(monkeypatch, [_RICH_ROW])
    svc.commit_import(_RICH_PDF, account_id=checking["id"])
    from backend.services import ledger as ledger_service
    from backend.services import payee_registry

    txn = ledger_service.transactions.all()[0]
    # description keeps the RAW descriptor (cross-source match signal + nothing lost)...
    assert txn["description"] == "MPOS*LUNARA CAFE DIST7"
    # ...while a CLEAN merchant entity is attached via payee_id.
    assert txn.get("payee_id")
    payee = payee_registry.require_payee(txn["payee_id"])
    assert payee["name"] == "Lunara"
    # Foreign charge persisted on the real fields.
    assert txn["original_amount"] == 845000.0 and txn["original_currency"] == "VND"
    # Ref-based dedup id persisted.
    assert primary_source(txn) == "statement"
    # ONE deterministic statement ref per row; the legacy "ref:"-prefixed twin is gone (b7f607da8).
    assert any(
        e.get("source") == "statement" and e.get("ref") and not e["ref"].startswith("ref:")
        for e in txn["external_refs"]
    )
    # Transient/display-only keys must NOT leak into the stored row.
    for junk in ("payee", "import_description", "reference"):
        assert junk not in txn, f"transient key {junk!r} leaked into the stored txn"


def test_reference_makes_reimport_dedup_even_if_amount_reformats(checking, monkeypatch):
    # Same bank Ref No. → same external_ref → a re-import (even with a slightly different
    # description/amount rendering) is an exact dup, never a double-book.
    _stub_llm(monkeypatch, [_RICH_ROW])
    first = svc.commit_import(_RICH_PDF, account_id=checking["id"])
    assert first["created"] == 1
    reworded = {**_RICH_ROW, "description": "LUNARA CAFE DOWNTOWN", "payee": "Lunara Cafe"}
    _stub_llm(monkeypatch, [reworded])
    second = svc.commit_import(_RICH_PDF, account_id=checking["id"])
    assert second["created"] == 0 and second["duplicates"] == 1


def test_same_currency_original_is_dropped_as_noise(checking, monkeypatch):
    # A "foreign" amount that equals the leg currency is not a real conversion — drop it.
    row = {**_RICH_ROW, "original_amount": 60.36, "original_currency": "USD"}  # acct is USD
    _stub_llm(monkeypatch, [row])
    prev = svc.preview_import(_RICH_PDF, account_id=checking["id"])
    assert prev.sample[0]["original_amount"] is None
    assert prev.sample[0]["original_currency"] is None


def test_no_reference_falls_back_to_content_hash(checking, monkeypatch):
    row = {k: v for k, v in _RICH_ROW.items() if k != "reference"}
    _stub_llm(monkeypatch, [row])
    prev = svc.preview_import(_RICH_PDF, account_id=checking["id"])
    assert primary_source(prev.sample[0]) == "statement"
    ref = primary_ref(prev.sample[0])
    assert ref and not ref.startswith("ref:")


# ── format detection ─────────────────────────────────────────────────────────────────


def test_format_detection():
    from backend.services import import_common

    # Explicit.
    assert import_common.detect_format("pdf", None, b"") == "pdf"
    # Extension.
    assert import_common.detect_format(None, "statement.pdf", b"") == "pdf"
    # No extension → content sniff on the %PDF- magic.
    assert import_common.detect_format(None, None, _SAMPLE_PDF) == "pdf"
    # A CSV is NOT mistaken for PDF.
    assert import_common.detect_format(None, None, b"Date,Amount\n2026-01-01,5\n") == "csv"


# ── real LLM round-trip (integration — skipped without a key) ─────────────────────────


@pytest.mark.integration
def test_real_llm_parse(checking):
    """End-to-end with the ACTUAL provider (no stub): a clean two-row statement PDF parses
    into a booked expense + income. Skipped unless ANTHROPIC_API_KEY is set (no network in
    the unit suite). Registers the real provider on the seam via ai/tools import."""
    import backend.ai.tools  # noqa: F401 — registers the LLM provider on core.llm_seam

    res = svc.commit_import(_SAMPLE_PDF, account_id=checking["id"])
    assert res["created"] >= 1
    assert res["total_rows"] >= 1
