"""REST smoke for the mapped-CSV import endpoints (preview + commit).

TestClient on a bare app with ONLY the import router mounted; the ledger's account/txn
stores are isolated to tmp_path. Verifies the preview returns its shape and writes
nothing, the commit creates rows, a re-import dedups to zero, and the CSRF guard +
malformed-mapping path return the right status codes."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.txn_sources import primary_ref, primary_source


@pytest.fixture
def ctx(tmp_path, monkeypatch, fx_rates):
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)

    acct = accounts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "USD"})

    app = FastAPI()
    from backend.api.import_api import router

    app.include_router(router)
    return TestClient(app), acct, txns


_CSV = b"""Date,Memo,Amount
2026-01-02,Coffee,-4.50
2026-01-03,Paycheck,2500.00
"""

_MAPPING = json.dumps({"date": "Date", "description": "Memo", "amount": "Amount"})

# The export-style CSRF guard accepts this header in lieu of a Bearer token.
_HDR = {"X-Requested-With": "XMLHttpRequest"}


def _files():
    return {"file": ("bank.csv", _CSV, "text/csv")}


def test_preview_shape_and_writes_nothing(ctx):
    client, acct, txns = ctx
    resp = client.post(
        "/api/statements/csv/preview",
        files=_files(),
        data={"mapping": _MAPPING, "account_id": acct["id"]},
        headers=_HDR,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 2
    assert body["importable"] == 2
    assert body["duplicates"] == 0
    assert len(body["sample"]) == 2
    assert set(body) >= {"columns", "total_rows", "importable", "duplicates", "skipped", "sample"}
    # Preview must not have written.
    assert txns.all() == []


def test_commit_creates_then_dedups(ctx):
    client, acct, txns = ctx
    first = client.post(
        "/api/statements/csv",
        files=_files(),
        data={"mapping": _MAPPING, "account_id": acct["id"]},
        headers=_HDR,
    )
    assert first.status_code == 200
    body = first.json()
    # The one-shot commit now rides the decision pipeline: same counts PLUS the pipeline
    # evidence — an import batch (rollback) and a filed statement record.
    assert body["created"] == 2 and body["duplicates"] == 0
    assert body["skipped"] == 0 and body["total_rows"] == 2
    assert body["batch_id"] and body["statement_record_id"]
    rows = txns.all()
    assert len(rows) == 2
    # Fingerprints are minted — the hard-dedup floor for every future re-import.
    assert all(primary_source(r) == "statement" for r in rows)

    second = client.post(
        "/api/statements/csv",
        files=_files(),
        data={"mapping": _MAPPING, "account_id": acct["id"]},
        headers=_HDR,
    )
    assert second.json()["created"] == 0
    assert second.json()["duplicates"] == 2
    assert len(txns.all()) == 2


def test_csrf_guard_blocks_without_header(ctx):
    client, acct, _ = ctx
    resp = client.post(
        "/api/statements/csv",
        files=_files(),
        data={"mapping": _MAPPING, "account_id": acct["id"]},
    )
    assert resp.status_code == 403


def test_bad_mapping_returns_422(ctx):
    client, acct, _ = ctx
    resp = client.post(
        "/api/statements/csv/preview",
        files=_files(),
        data={
            "mapping": json.dumps({"description": "Memo", "amount": "Amount"}),
            "account_id": acct["id"],
        },
        headers=_HDR,
    )
    assert resp.status_code == 422  # missing date mapping


def test_malformed_mapping_json_returns_422(ctx):
    client, acct, _ = ctx
    resp = client.post(
        "/api/statements/csv/preview",
        files=_files(),
        data={"mapping": "{not json", "account_id": acct["id"]},
        headers=_HDR,
    )
    assert resp.status_code == 422


# ── OFX / QIF via the generic format-aware endpoints ───────────────────────────────

_OFX = b"""OFXHEADER:100
<OFX>
<BANKACCTFROM><ACCTID>ACC-1</ACCTID></BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><DTPOSTED>20260102<TRNAMT>-4.50<FITID>F1<NAME>Coffee</STMTTRN>
<STMTTRN><DTPOSTED>20260103<TRNAMT>2500.00<FITID>F2<NAME>Paycheck</STMTTRN>
</BANKTRANLIST>
</OFX>
"""

_QIF = b"""!Type:Bank
D01/02/2026
T-4.50
PCoffee
^
D01/03/2026
T2500.00
PPaycheck
^
"""


def test_ofx_preview_and_commit_autodetect(ctx):
    client, acct, txns = ctx
    files = {"file": ("statement.ofx", _OFX, "application/octet-stream")}
    prev = client.post(
        "/api/statements/preview", files=files, data={"account_id": acct["id"]}, headers=_HDR
    )
    assert prev.status_code == 200
    assert prev.json()["importable"] == 2
    # OFX now rides the decision pipeline: a cached statement record + uncapped rows with
    # the format-NATIVE (external_source, external_ref) (legacy `ofx` + `<acct>:<FITID>`, byte-identical).
    assert prev.json()["statement_id"].startswith("stmt")
    assert {(primary_source(r), primary_ref(r)) for r in prev.json()["rows"]} == {
        ("ofx", "ACC-1:F1"),
        ("ofx", "ACC-1:F2"),
    }
    assert txns.all() == []  # preview writes nothing

    commit = client.post(
        "/api/statements/import", files=files, data={"account_id": acct["id"]}, headers=_HDR
    )
    assert commit.status_code == 200
    body = commit.json()
    assert body["created"] == 2 and body["duplicates"] == 0
    assert body["skipped"] == 0 and body["total_rows"] == 2
    assert body["batch_id"] and body["statement_record_id"]
    # Re-import → zero duplicates (idempotent on FITID).
    again = client.post(
        "/api/statements/import", files=files, data={"account_id": acct["id"]}, headers=_HDR
    )
    assert again.json()["created"] == 0
    assert again.json()["duplicates"] == 2


def test_qif_commit_explicit_format(ctx):
    client, acct, txns = ctx
    files = {"file": ("statement.qif", _QIF, "application/octet-stream")}
    commit = client.post(
        "/api/statements/import",
        files=files,
        data={"account_id": acct["id"], "format": "qif"},
        headers=_HDR,
    )
    assert commit.status_code == 200
    assert commit.json()["created"] == 2
    assert len(txns.all()) == 2


def test_generic_csrf_guard_blocks(ctx):
    client, acct, _ = ctx
    resp = client.post(
        "/api/statements/import",
        files={"file": ("statement.ofx", _OFX, "application/octet-stream")},
        data={"account_id": acct["id"]},
    )
    assert resp.status_code == 403


def test_unsupported_format_returns_422(ctx):
    client, acct, _ = ctx
    resp = client.post(
        "/api/statements/preview",
        files=_files(),
        data={"account_id": acct["id"], "format": "xlsx"},
        headers=_HDR,
    )
    assert resp.status_code == 422


# ── password-protected xlsx (REST contract) ─────────────────────────────────────────
# msoffcrypto is decrypt-only, so a real encrypted OOXML fixture can't be authored in a
# test — the encryption boundary is faked at the services.office_crypt seam instead.


class _FakeEncryptedOffice:
    def __init__(self, _f):
        pass

    def is_encrypted(self):
        return True

    def load_key(self, password):
        if password != "secret":
            raise ValueError("bad key")

    def decrypt(self, out):
        import io

        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date", "Memo", "Amount"])
        ws.append(["2026-01-02", "Coffee", -4.50])
        ws.append(["2026-01-03", "Paycheck", 2500.00])
        buf = io.BytesIO()
        wb.save(buf)
        out.write(buf.getvalue())


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_encrypted_xlsx_preview_without_password_is_422_password_required(ctx, monkeypatch):
    """Was: openpyxl's raw ``BadZipFile`` ("Not a readable Excel (.xlsx) file: File is not a
    zip file") — no hint it's a password problem."""
    import backend.services.office_crypt as office_crypt

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    client, acct, _ = ctx
    resp = client.post(
        "/api/statements/preview",
        files={"file": ("statement.xlsx", b"encrypted-bytes", _XLSX_MIME)},
        data={"mapping": _MAPPING, "account_id": acct["id"]},
        headers=_HDR,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("password_required:")


def test_encrypted_xlsx_preview_wrong_password_is_a_distinct_422(ctx, monkeypatch):
    import backend.services.office_crypt as office_crypt

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    client, acct, _ = ctx
    resp = client.post(
        "/api/statements/preview",
        files={"file": ("statement.xlsx", b"encrypted-bytes", _XLSX_MIME)},
        data={"mapping": _MAPPING, "account_id": acct["id"], "password": "wrong"},
        headers=_HDR,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("Wrong password")


def test_encrypted_xlsx_preview_and_commit_with_correct_password(ctx, monkeypatch):
    import backend.services.office_crypt as office_crypt

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _FakeEncryptedOffice)
    client, acct, txns = ctx
    prev = client.post(
        "/api/statements/preview",
        files={"file": ("statement.xlsx", b"encrypted-bytes", _XLSX_MIME)},
        data={"mapping": _MAPPING, "account_id": acct["id"], "password": "secret"},
        headers=_HDR,
    )
    assert prev.status_code == 200
    assert prev.json()["total_rows"] == 2
    assert txns.all() == []  # preview writes nothing

    commit = client.post(
        "/api/statements/import",
        files={"file": ("statement.xlsx", b"encrypted-bytes", _XLSX_MIME)},
        data={"mapping": _MAPPING, "account_id": acct["id"], "password": "secret"},
        headers=_HDR,
    )
    assert commit.status_code == 200
    assert commit.json()["created"] == 2
    assert len(txns.all()) == 2


def test_statement_summary_treats_legacy_null_status_as_ready_when_parsed():
    """Code review round B, nit 3: a pre-B20 record's ``status`` column is NULL (the
    column never existed for it) — the ADR says treat that as "ready" once it actually
    has parsed rows, else leave it unknown (no lifecycle chrome). An explicit status
    (even something as final as "failed") is never overridden by ``has_parsed_rows``."""
    from backend.api.import_api import _statement_summary

    legacy_parsed = _statement_summary({"status": None, "has_parsed_rows": True})
    assert legacy_parsed["parse_status"] == "ready"

    legacy_unparsed = _statement_summary({"status": None, "has_parsed_rows": False})
    assert legacy_unparsed["parse_status"] is None

    explicit_status = _statement_summary({"status": "failed", "has_parsed_rows": True})
    assert explicit_status["parse_status"] == "failed"
