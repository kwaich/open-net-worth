"""Regression: PDF/MT940/CAMT imports must NOT silently drop genuine identical twin rows.

F01 (CRITICAL) — two identical real charges (same date/amount/description) previously hashed to
the SAME content-hash external_ref; flag_duplicates then marked the second a duplicate and
build_commit dropped it — ledger short vs the bank, import summary falsely showed duplicates:1.

The fix: call disambiguate_identical_rows(results) BEFORE flag_duplicates(results) in each
importer's _map_rows, exactly mirroring import_csv.py. This test file confirms:

  1. Two identical rows → BOTH created; second gets the :1 suffix on its external_ref.
  2. Re-import of same file → ZERO new rows (idempotent; both bare and :1 refs hit the floor).
  3. CAMT only: an entry WITH a native ref is unaffected (id unchanged, no suffix).

PDF is tested at the _map_rows/row level with synthetic raw rows (below the LLM seam).
MT940 and CAMT are tested end-to-end with commit_import on crafted format bytes.

── Second, unrelated sense of "twin" — GONE ─────────────────────────────────────────────
This file used to carry a second half pinning the STATEMENT twin matcher
(``ledger_diff._annotate_statement_twins`` → ``statement_twin`` / ``twin_eligible``), which
answered the inverse question — "which uncovered LEDGER entry is this statement row?" — and
shared only the word. RFC ``.claude/rfcs/2026-07-29-one-match-engine-instructions-as-policy.md``
§D2/§D3 deleted that matcher: its settled-drift tier is now ``matching._classify_settled_drift``,
inside the one engine, and the inverse view is a pure set difference over the flags the engine
already produced. Those pins moved with the behaviour, to
``tests/dedup/test_twin_invariant_closure.py`` and ``tests/dedup/test_golden_books.py``.
"""

from __future__ import annotations

import pytest

from backend.core.txn_sources import primary_ref, primary_source
from backend.services import ledger as ledger_service

# ── shared store isolation ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch):
    """Rebind the ledger singletons to fresh per-test tmp files."""
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    return accounts, txns


@pytest.fixture
def checking_usd(isolate_stores, fx_rates):
    accounts, _ = isolate_stores
    return accounts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "USD"})


@pytest.fixture
def checking_eur(isolate_stores, fx_rates):
    accounts, _ = isolate_stores
    return accounts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "EUR"})


# ── PDF twins ────────────────────────────────────────────────────────────────────────


def _map_pdf_rows(raw: list[dict], account_id: str):
    """The old import_pdf._map_rows path, via the unified parser's row pipeline."""
    from backend.services.statements import importer, tiers
    from backend.services.statements.canonical import ParseResult, StatementMeta

    rows = [tiers.to_canonical(r) for r in raw]
    result = ParseResult(meta=StatementMeta(), rows=rows, tier="text")
    return importer.rows_to_results(result, account_id, prefix="pdf")


def test_pdf_twin_rows_both_created(checking_usd):
    """Two identical extracted PDF rows produce TWO transactions — not one (F01 fix)."""
    twin = {
        "date": "2026-01-05",
        "amount": 5.00,
        "direction": "OUT",
        "note": "Coffee",
        "confidence": 0.9,
    }
    raw = [twin, twin]  # identical dicts — same hash without disambiguation

    results = _map_pdf_rows(raw, checking_usd["id"])

    created = [r for r in results if r.draft and not r.duplicate]
    assert len(created) == 2, "both twin rows must be importable"


def test_pdf_twin_second_row_gets_suffix(checking_usd):
    """The second identical PDF row must carry the :1 suffix on its external_ref."""
    twin = {
        "date": "2026-01-05",
        "amount": 5.00,
        "direction": "OUT",
        "note": "Coffee",
        "confidence": 0.9,
    }
    raw = [twin, twin]

    results = _map_pdf_rows(raw, checking_usd["id"])

    ids = [primary_ref(r.draft) for r in results if r.draft]
    assert len(ids) == 2
    bare, suffixed = ids
    assert suffixed == bare + ":1", f"second id should be '{bare}:1', got '{suffixed}'"


def test_pdf_reimport_is_idempotent(checking_usd):
    """Re-importing the same twin PDF → 0 new rows (both bare and :1 ids already in ledger)."""
    from backend.services.import_common import build_commit

    twin = {
        "date": "2026-01-05",
        "amount": 5.00,
        "direction": "OUT",
        "note": "Coffee",
        "confidence": 0.9,
    }
    raw = [twin, twin]

    # First import
    results1 = _map_pdf_rows(raw, checking_usd["id"])
    result1 = build_commit(len(raw), results1)
    assert result1["created"] == 2

    # Second import of the same rows
    results2 = _map_pdf_rows(raw, checking_usd["id"])
    result2 = build_commit(len(raw), results2)
    assert result2["created"] == 0
    assert result2["duplicates"] == 2


def test_pdf_import_summary_shows_zero_duplicates(checking_usd):
    """The import summary must report duplicates:0 when both twins are genuine new rows."""
    from backend.services.import_common import build_commit

    twin = {
        "date": "2026-01-05",
        "amount": 5.00,
        "direction": "OUT",
        "note": "Coffee",
        "confidence": 0.9,
    }
    raw = [twin, twin]

    results = _map_pdf_rows(raw, checking_usd["id"])
    summary = build_commit(len(raw), results)

    assert summary["duplicates"] == 0
    assert summary["created"] == 2


# ── MT940 twins ──────────────────────────────────────────────────────────────────────

# Two identical :61: lines with NONREF (the common real-world collision path).
_MT940_TWINS = b""":20:STMT-TWIN
:25:NL12ABNA0123456789
:28C:00001/001
:60F:C260101EUR1000,00
:61:2601050105DR5,00NTRFNONREF//X1
:86:Coffee Shop
:61:2601050105DR5,00NTRFNONREF//X1
:86:Coffee Shop
:62F:C260105EUR990,00
-
"""


def test_mt940_twin_rows_both_created(checking_eur):
    """Two identical MT940 :61: lines produce TWO transactions (F01 fix)."""
    from backend.services import import_mt940 as svc

    result = svc.commit_import(_MT940_TWINS, account_id=checking_eur["id"])
    assert result["created"] == 2
    assert result["duplicates"] == 0


def test_mt940_twin_second_row_gets_suffix(checking_eur):
    """The second identical MT940 row carries the :1 suffix on its external_ref."""
    from backend.services import import_mt940 as svc

    svc.commit_import(_MT940_TWINS, account_id=checking_eur["id"])

    txns = ledger_service.transactions.all()
    ext_ids = sorted(primary_ref(t) for t in txns)
    assert len(ext_ids) == 2
    bare, suffixed = ext_ids
    assert suffixed == bare + ":1", f"second id should be '{bare}:1', got '{suffixed}'"


def test_mt940_reimport_is_idempotent(checking_eur):
    """Re-importing the same MT940 twin file → 0 new rows."""
    from backend.services import import_mt940 as svc

    r1 = svc.commit_import(_MT940_TWINS, account_id=checking_eur["id"])
    assert r1["created"] == 2

    r2 = svc.commit_import(_MT940_TWINS, account_id=checking_eur["id"])
    assert r2["created"] == 0
    assert r2["duplicates"] == 2


# ── CAMT twins ───────────────────────────────────────────────────────────────────────

# Two identical entries without a native ref (the content-hash path).
_CAMT_TWINS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt>
    <Stmt>
      <Acct><Id><IBAN>NL12ABNA0123456789</IBAN></Id></Acct>
      <Ntry>
        <Amt Ccy="EUR">5.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><Dt>2026-01-05</Dt></BookgDt>
        <NtryDtls><TxDtls><RmtInf><Ustrd>Coffee Shop</Ustrd></RmtInf></TxDtls></NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="EUR">5.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><Dt>2026-01-05</Dt></BookgDt>
        <NtryDtls><TxDtls><RmtInf><Ustrd>Coffee Shop</Ustrd></RmtInf></TxDtls></NtryDtls>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>
"""

# A CAMT entry WITH a native AcctSvcrRef — must stay untouched by disambiguation.
_CAMT_WITH_REF = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt>
    <Stmt>
      <Acct><Id><IBAN>NL12ABNA0123456789</IBAN></Id></Acct>
      <Ntry>
        <Amt Ccy="EUR">5.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><Dt>2026-01-05</Dt></BookgDt>
        <NtryDtls><TxDtls>
          <Refs><AcctSvcrRef>BANKREF123</AcctSvcrRef></Refs>
          <RmtInf><Ustrd>Coffee Shop</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>
"""


def test_camt_twin_rows_both_created(checking_eur):
    """Two identical CAMT entries (no native ref) produce TWO transactions (F01 fix)."""
    from backend.services import import_camt as svc

    result = svc.commit_import(_CAMT_TWINS, account_id=checking_eur["id"])
    assert result["created"] == 2
    assert result["duplicates"] == 0


def test_camt_twin_second_row_gets_suffix(checking_eur):
    """The second identical CAMT no-ref entry carries the :1 suffix on its external_ref."""
    from backend.services import import_camt as svc

    svc.commit_import(_CAMT_TWINS, account_id=checking_eur["id"])

    txns = ledger_service.transactions.all()
    ext_ids = sorted(primary_ref(t) for t in txns)
    assert len(ext_ids) == 2
    bare, suffixed = ext_ids
    assert suffixed == bare + ":1", f"second id should be '{bare}:1', got '{suffixed}'"


def test_camt_reimport_is_idempotent(checking_eur):
    """Re-importing the same CAMT twin file → 0 new rows."""
    from backend.services import import_camt as svc

    r1 = svc.commit_import(_CAMT_TWINS, account_id=checking_eur["id"])
    assert r1["created"] == 2

    r2 = svc.commit_import(_CAMT_TWINS, account_id=checking_eur["id"])
    assert r2["created"] == 0
    assert r2["duplicates"] == 2


def test_camt_native_ref_unaffected_by_disambiguation(checking_eur):
    """A CAMT entry WITH an AcctSvcrRef keeps its native id — no suffix appended."""
    from backend.services import import_camt as svc

    result = svc.commit_import(_CAMT_WITH_REF, account_id=checking_eur["id"])
    assert result["created"] == 1

    txns = ledger_service.transactions.all()
    assert len(txns) == 1
    # Native ref path: external_source="camt", external_ref="<acct>:<ref>" — no hash, no :1 suffix.
    assert primary_source(txns[0]) == "statement"
    assert primary_ref(txns[0]) == "NL12ABNA0123456789:BANKREF123"
