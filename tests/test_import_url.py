"""Import from a URL / Google Sheet (CONN — hosted-CSV import).

Two layers, the network always MOCKED (no live fetch; the live path is integration-gated):
  * the service helper ``import_common.fetch_url_bytes`` — happy path, the size cap (413),
    a bad scheme / non-2xx / empty body / transport error (all user-correctable), and the
    filename-hint it derives from the final (redirected) URL for format detection.
  * the REST endpoints — a ``source_url`` preview/commit round-trip on the SAME core as an
    upload, idempotent re-import from the same URL (zero dups), format auto-detect from the
    URL content (OFX), the columns-probe, a bad URL → 422, and the file-XOR-url guard.

Mirrors ``tests/test_import_api.py``: a bare app with only the import router mounted; the
ledger's account/txn stores isolated to tmp_path. ``httpx.get`` is monkeypatched on the
service module, so no test touches the network."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import import_common
from backend.services.import_common import ImportError_, ImportTooLarge, fetch_url_bytes

_CSV = b"""Date,Memo,Amount
2026-01-02,Coffee,-4.50
2026-01-03,Paycheck,2500.00
"""

_OFX = b"""OFXHEADER:100
<OFX>
<BANKACCTFROM><ACCTID>ACC-1</ACCTID></BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><DTPOSTED>20260102<TRNAMT>-4.50<FITID>F1<NAME>Coffee</STMTTRN>
<STMTTRN><DTPOSTED>20260103<TRNAMT>2500.00<FITID>F2<NAME>Paycheck</STMTTRN>
</BANKTRANLIST>
</OFX>
"""

# The export-style CSRF guard accepts this header in lieu of a Bearer token.
_HDR = {"X-Requested-With": "XMLHttpRequest"}

# The mapping the UI builds for the demo CSV (auto-mapped from the headers).
_MAPPING = '{"date": "Date", "description": "Memo", "amount": "Amount"}'


class _Resp:
    """A minimal stand-in for what ``pinned_urlopen`` returns — an ``HTTPResponse``-shaped
    context manager, not an httpx.Response.

    `fetch_url_bytes` moved off `httpx.get(follow_redirects=True)` onto
    `core.pinned_http.pinned_urlopen` so the URL is SSRF-guarded and every redirect hop is
    re-pinned (audit FINDING 12 — `source_url` is model-supplied, so the URL is attacker-
    reachable through prompt injection). These stubs follow that shape: `.status`,
    `.geturl()`, `.read(n)`, and the context-manager protocol.
    """

    def __init__(self, content: bytes, status_code: int = 200, url: str = "https://x/feed.csv"):
        self._content = content
        self.status = status_code
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def geturl(self):
        return self._url

    def read(self, _n=None):
        return self._content


def _mock_fetch(monkeypatch, resp: _Resp):
    monkeypatch.setattr(import_common, "pinned_urlopen", lambda *a, **k: resp)


# ── service helper: fetch_url_bytes ────────────────────────────────────────────────


def test_fetch_url_bytes_happy(monkeypatch):
    _mock_fetch(monkeypatch, _Resp(_CSV, url="https://host/path/statement.csv?gid=0"))
    content, hint = fetch_url_bytes("https://host/path/statement.csv?gid=0")
    assert content == _CSV
    # filename hint = last path segment of the (redirected) URL, sans query.
    assert hint == "statement.csv"


def test_fetch_url_bytes_rejects_bad_scheme():
    with pytest.raises(ImportError_):
        fetch_url_bytes("ftp://host/file.csv")
    with pytest.raises(ImportError_):
        fetch_url_bytes("")


def test_fetch_url_bytes_non_2xx(monkeypatch):
    _mock_fetch(monkeypatch, _Resp(b"Not Found", status_code=404))
    with pytest.raises(ImportError_):
        fetch_url_bytes("https://host/missing.csv")


def test_fetch_url_bytes_empty_body(monkeypatch):
    _mock_fetch(monkeypatch, _Resp(b"   \n"))
    with pytest.raises(ImportError_):
        fetch_url_bytes("https://host/empty.csv")


def test_fetch_url_bytes_transport_error(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(import_common, "pinned_urlopen", boom)
    with pytest.raises(ImportError_):
        fetch_url_bytes("https://unreachable/file.csv")


def test_fetch_url_bytes_size_cap(monkeypatch):
    big = b"x" * (import_common.MAX_FETCH_BYTES + 1)
    _mock_fetch(monkeypatch, _Resp(big))
    with pytest.raises(ImportTooLarge):
        fetch_url_bytes("https://host/huge.csv")


# ── REST: source_url preview / commit ──────────────────────────────────────────────


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


def test_url_csv_preview_and_commit_then_dedup(ctx, monkeypatch):
    client, acct, txns = ctx
    _mock_fetch(monkeypatch, _Resp(_CSV, url="https://docs.google.com/sheet/pub.csv"))

    prev = client.post(
        "/api/statements/preview",
        data={
            "source_url": "https://docs.google.com/sheet/pub?output=csv",
            "mapping": _MAPPING,
            "account_id": acct["id"],
        },
        headers=_HDR,
    )
    assert prev.status_code == 200
    assert prev.json()["importable"] == 2
    assert txns.all() == []  # preview writes nothing

    commit = client.post(
        "/api/statements/import",
        data={
            "source_url": "https://docs.google.com/sheet/pub?output=csv",
            "mapping": _MAPPING,
            "account_id": acct["id"],
        },
        headers=_HDR,
    )
    assert commit.status_code == 200
    body = commit.json()
    # The one-shot URL commit rides the decision pipeline too: same counts PLUS the
    # pipeline evidence (import batch → rollback, filed statement record).
    assert body["created"] == 2 and body["duplicates"] == 0
    assert body["skipped"] == 0 and body["total_rows"] == 2
    assert body["batch_id"] and body["statement_record_id"]

    # Re-import from the SAME url → idempotent (content-addressed external_ref), zero dups.
    again = client.post(
        "/api/statements/import",
        data={
            "source_url": "https://docs.google.com/sheet/pub?output=csv",
            "mapping": _MAPPING,
            "account_id": acct["id"],
        },
        headers=_HDR,
    )
    assert again.json()["created"] == 0
    assert again.json()["duplicates"] == 2
    assert len(txns.all()) == 2


def test_url_format_autodetect_ofx(ctx, monkeypatch):
    client, acct, txns = ctx
    _mock_fetch(monkeypatch, _Resp(_OFX, url="https://bank/export.ofx"))
    commit = client.post(
        "/api/statements/import",
        data={"source_url": "https://bank/export.ofx", "account_id": acct["id"]},
        headers=_HDR,
    )
    assert commit.status_code == 200
    assert commit.json()["created"] == 2  # OFX needs no mapping, auto-detected from URL content


def test_url_columns_probe(ctx, monkeypatch):
    client, acct, _ = ctx
    _mock_fetch(monkeypatch, _Resp(_CSV, url="https://host/feed.csv"))
    resp = client.post(
        "/api/statements/preview",
        data={"source_url": "https://host/feed.csv", "format": "auto", "columns_only": "true"},
        headers=_HDR,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["format"] == "csv"
    assert body["columns"] == ["Date", "Memo", "Amount"]


def test_bad_url_returns_422(ctx, monkeypatch):
    client, acct, _ = ctx
    _mock_fetch(monkeypatch, _Resp(b"nope", status_code=403))
    resp = client.post(
        "/api/statements/preview",
        data={
            "source_url": "https://host/private.csv",
            "mapping": _MAPPING,
            "account_id": acct["id"],
        },
        headers=_HDR,
    )
    assert resp.status_code == 422


def test_neither_file_nor_url_returns_422(ctx):
    client, acct, _ = ctx
    resp = client.post(
        "/api/statements/preview",
        data={"mapping": _MAPPING, "account_id": acct["id"]},
        headers=_HDR,
    )
    assert resp.status_code == 422
