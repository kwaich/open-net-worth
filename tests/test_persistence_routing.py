"""Regression tests for the persistence + routing fixes.

FIX A (superseded 2026-08-16, cross-channel-attachments RFC Phase 4 /
        STATEMENT-ATTACHMENT-BOUNDARY): /api/upload used to ALSO persist every upload as a
        StatementStore record — that unconditional coupling is REMOVED
        (docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md): a generic
        chat upload creates only an AttachmentStore row. The "XLSX with raw bytes in
        upload_entry must not crash statement persistence" regression this section used to
        cover now lives on the explicit promotion path (import_transactions/
        decision_preview), which is where StatementStore.create is reached from now.
FIX B: import_transactions accepts statement_id so the user never needs to
        re-upload after the 30-min cache expires.
FIX C: get_statement resolves live ref_ids transparently and gives an actionable
        error for expired/unknown ids.
FIX D: mapping errors from CSV/XLSX imports include a diff_statement fallback hint.
"""

from __future__ import annotations

import asyncio
import io
import json

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Memo", "Amount"])
    ws.append(["2026-01-02", "Coffee", -4.50])
    wb_bytes = io.BytesIO()
    wb.save(wb_bytes)
    return wb_bytes.getvalue()


def _make_csv() -> bytes:
    return b"Date,Memo,Amount\n2026-01-02,Coffee,-4.50\n2026-01-03,Paycheck,2500.00\n"


def _call_tool(name: str, **kwargs) -> dict:
    from backend.ai.tools import execute_tool

    text, _ = asyncio.run(execute_tool(name, kwargs))
    return json.loads(text)


# ── fixture: isolated statement store + upload cache ─────────────────────────


@pytest.fixture(autouse=True)
def _isolated_uploads(tmp_path, monkeypatch, db_isolated):
    """Isolate the persistent attachment store + clear the in-process read cache —
    metadata via db_isolated (DB-backed, plan 4e task 1), the blob dir still a real
    tmp_path directory (ATTACHMENTS_DIR stays a Tier-4 file dir)."""
    import backend.db.attachment_store as as_mod
    from backend.services import attachments as attachments_svc

    up_dir = tmp_path / "attachments-iso"
    up_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(as_mod, "ATTACHMENTS_DIR", up_dir)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()


@pytest.fixture()
def statement_dir(tmp_path, monkeypatch, db_isolated):
    """Redirect statement store (DB-backed now, plan 3 task 4 — db_isolated redirects it,
    and every other unpinned store, to a fresh, empty database) + files dir to tmp_path."""
    stmts_json = tmp_path / "statements.json"
    stmts_dir = tmp_path / "statements"
    stmts_dir.mkdir()

    import backend.core.paths as core_paths_mod
    import backend.db.statement_store as ss_mod
    import backend.paths as paths_mod

    monkeypatch.setattr(paths_mod, "STATEMENTS_JSON", stmts_json)
    monkeypatch.setattr(paths_mod, "STATEMENTS_FILES_DIR", stmts_dir)
    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path)

    # The store's byte-file path helper (read_file_bytes) still reads DATA_DIR directly
    # (imported at module level from backend.core.paths) — patch on the module too, kept
    # aligned with DATA_DIR above. Every consumer of the statements blob directory now
    # reads through backend.core.paths.statements_files_dir() (Fix 3, security review
    # round 2) — patch its ONE real binding instead of walking every consumer module.
    monkeypatch.setattr(ss_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", stmts_dir)
    return tmp_path


@pytest.fixture()
def upload_client(statement_dir):
    """A TestClient with ONLY the upload router, stores redirected to tmp_path."""
    from backend.api.upload import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def account(tmp_path, monkeypatch, db_isolated):
    """Accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects
    them (and every other unpinned store) to a fresh, empty database."""
    from backend.db.account_store import AccountStore
    from backend.services import ledger as ledger_service

    accts = AccountStore()
    monkeypatch.setattr(ledger_service, "accounts", accts)
    from backend.db.transaction_store import TransactionStore

    txns = TransactionStore()
    monkeypatch.setattr(ledger_service, "transactions", txns)
    return accts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "VND"})


# ── FIX A (superseded, Phase 4): /api/upload creates NO statement record ──────


def test_xlsx_upload_creates_attachment_only(upload_client, statement_dir):
    """POST /api/upload with an XLSX creates only an AttachmentStore row — no
    StatementStore side effect (STATEMENT-ATTACHMENT-BOUNDARY, item 21)."""
    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    xlsx_bytes = _make_xlsx()
    # Use a Vietnamese filename with accented characters — the original FIX A incident filename
    resp = upload_client.post(
        "/api/upload",
        files={"file": ("Sao kê tài khoản.xlsx", xlsx_bytes, xlsx_mime)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "text"
    assert "statement_id" not in body, "upload response must not carry a statement_id anymore"

    from backend.db.statement_store import StatementStore

    assert StatementStore().all() == [], "a generic upload must create no statement record"


def test_upload_persists_no_statement_for_csv_either(upload_client, statement_dir):
    """CSV uploads are also attachment-only now — the OLD 'CSV is statement-kind so it
    persists' assumption is exactly the coupling Phase 4 removes."""
    resp = upload_client.post(
        "/api/upload",
        files={"file": ("bank.csv", _make_csv(), "text/csv")},
    )
    assert resp.status_code == 200
    assert "statement_id" not in resp.json()

    from backend.db.statement_store import StatementStore

    assert StatementStore().all() == []


def test_upload_entry_bytes_do_not_break_the_explicit_import_path(
    upload_client, statement_dir, account
):
    """The FIX A root cause (raw 'content' bytes in upload_entry breaking JSON
    serialisation) must still be fixed on the path that NOW reaches StatementStore.create —
    the explicit import_transactions promotion, not the generic upload."""
    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    xlsx_bytes = _make_xlsx()
    resp = upload_client.post(
        "/api/upload",
        files={"file": ("statement.xlsx", xlsx_bytes, xlsx_mime)},
    )
    assert resp.status_code == 200
    ref_id = resp.json()["ref_id"]

    result = _call_tool(
        "import_transactions",
        attachment_ref=ref_id,
        account_id=account["id"],
        mapping={"date": "Date", "description": "Memo", "amount": "Amount"},
    )
    assert result["ok"] is True, result
    sid = result["statement_id"]

    from backend.db.statement_store import StatementStore

    record = StatementStore().find_by_id(sid)
    assert record is not None
    assert "content" not in record.get("upload_entry", {}), (
        "'content' bytes must be stripped from stored upload_entry"
    )
    assert record.get("source_attachment_id") == ref_id, (
        "the promoted statement must record its source attachment (item 22 provenance)"
    )


# ── FIX A: dedup in statement_store does not break on bytes-bearing entry ──────


def test_statement_store_create_with_bytes_in_entry(statement_dir):
    """StatementStore.create must succeed even when upload_entry has 'content' bytes."""
    from backend.db.statement_store import StatementStore

    store = StatementStore()

    xlsx_bytes = _make_xlsx()
    # Simulate what parse_upload returns: a dict with raw bytes in 'content'
    entry_with_bytes = {
        "kind": "text",
        "text": "[Sheet: Sheet]\nDate,Memo,Amount\n",
        "summary": "Excel file",
        "format": "xlsx",
        "content": xlsx_bytes,  # <-- bytes: was the root cause
        "was_encrypted": False,  # non-JSON-able would have been here too if bool were an issue
    }
    record = store.create(
        "test.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        xlsx_bytes,
        entry_with_bytes,
    )
    assert record["id"] is not None
    assert "content" not in record.get("upload_entry", {}), "bytes must be stripped"

    # Dedup path: same file again must not blow up either
    record2 = store.create(
        "test.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        xlsx_bytes,
        entry_with_bytes,
    )
    assert record2["id"] == record["id"], "dedup must return the same record"


def test_statement_store_read_file_bytes(statement_dir):
    """read_file_bytes uses DATA_DIR, not __file__.parent.parent."""
    from backend.db.statement_store import StatementStore

    store = StatementStore()

    content = b"hello world"
    record = store.create("test.csv", "text/csv", content, {"kind": "text", "text": "hello"})
    restored = store.read_file_bytes(record["id"])
    assert restored == content, "read_file_bytes must return the original bytes"


# ── FIX B: import_transactions with statement_id ──────────────────────────────


def test_import_transactions_with_statement_id(statement_dir, account, monkeypatch):
    """import_transactions(statement_id=...) reads from disk when cache is empty."""
    # Patch store paths for import_transactions to use our tmp_path
    import backend.paths as paths_mod

    monkeypatch.setattr(paths_mod, "STATEMENTS_JSON", statement_dir / "statements.json")
    monkeypatch.setattr(paths_mod, "STATEMENTS_FILES_DIR", statement_dir / "statements")
    monkeypatch.setattr(paths_mod, "DATA_DIR", statement_dir)
    import backend.core.paths as core_paths_mod
    import backend.db.statement_store as ss_mod

    monkeypatch.setattr(ss_mod, "DATA_DIR", statement_dir)
    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", statement_dir / "statements")

    from backend.db.statement_store import StatementStore

    store = StatementStore()
    csv_bytes = _make_csv()
    entry = {"kind": "text", "text": csv_bytes.decode(), "format": "csv"}
    record = store.create("bank.csv", "text/csv", csv_bytes, entry)
    sid = record["id"]

    # No upload row exists for this statement (simulates a pre-persistence ref)
    mapping = {"date": "Date", "description": "Memo", "amount": "Amount"}
    result = _call_tool(
        "import_transactions",
        statement_id=sid,
        account_id=account["id"],
        mapping=mapping,
    )
    assert result["ok"] is True, result
    assert result["format"] == "csv"
    assert result["result"]["importable"] == 2


def test_import_transactions_unknown_statement_id(account):
    """import_transactions with an unknown statement_id returns a clear error."""
    result = _call_tool(
        "import_transactions",
        statement_id="does-not-exist",
        account_id=account["id"],
    )
    assert result["ok"] is False
    assert "not found" in result["error"].lower()
    assert "list_statements" in result["error"]


def test_import_transactions_missing_attachment_ref_error(account):
    """A ref with no attachment behind it (deleted, or a wrong id; attachments never expire)
    points to list_statements and statement_id, for a statement already on file."""
    result = _call_tool(
        "import_transactions",
        attachment_ref="00000000-0000-0000-0000-000000000000",
        account_id=account["id"],
    )
    assert result["ok"] is False
    assert "list_statements" in result["error"]
    assert "statement_id" in result["error"]


# ── FIX C: get_statement resolves ref_id transparently ────────────────────────


def test_get_statement_resolves_live_ref_id(statement_dir, monkeypatch):
    """get_statement with a live ref_id (that carries statement_id) returns the statement."""
    import backend.paths as paths_mod

    monkeypatch.setattr(paths_mod, "STATEMENTS_JSON", statement_dir / "statements.json")
    import backend.db.statement_store as ss_mod

    monkeypatch.setattr(ss_mod, "DATA_DIR", statement_dir)

    from backend.db.statement_store import StatementStore

    store = StatementStore()
    csv_bytes = _make_csv()
    entry = {"kind": "text", "text": csv_bytes.decode(), "format": "csv"}
    record = store.create("bank.csv", "text/csv", csv_bytes, entry)
    sid = record["id"]

    # Store an upload whose entry points to the statement
    from backend.services.attachments import create_attachment

    ref_id = create_attachment({"kind": "text", "statement_id": sid}, filename="bank.csv")

    result = _call_tool("get_statement", statement_id=ref_id)
    assert result["ok"] is True, result
    assert result["statement"]["id"] == sid
    assert result["statement"]["resolved_from_ref_id"] is True


def test_get_statement_live_ref_without_statement(statement_dir):
    """get_statement with a ref_id that has no statement_id linked returns actionable error."""
    from backend.services.attachments import create_attachment

    ref_id = create_attachment({"kind": "image"})  # no 'statement_id'

    result = _call_tool("get_statement", statement_id=ref_id)
    assert result["ok"] is False
    assert "ref_id" in result["error"]
    assert "list_statements" in result["error"]


def test_get_statement_expired_or_unknown_id(statement_dir):
    """get_statement with a completely unknown id returns a helpful error."""
    result = _call_tool("get_statement", statement_id="00000000-0000-0000-0000-deadbeefcafe")
    assert result["ok"] is False
    assert "30 minutes" in result["error"] or "list_statements" in result["error"]


# ── FIX D: mapping errors include the fallback workflow hint ──────────────────


def test_import_mapping_error_includes_fallback_hint(statement_dir, account, monkeypatch):
    """When CSV mapping fails ('Map a column to ...'), the error must include diff_statement hint."""
    # Upload a CSV via the upload cache directly (no column mapping provided)
    from backend.api.upload import parse_upload, store_upload

    csv_bytes = b"TransDate,Details,Debit,Credit\n2026-01-02,Coffee,4.50,\n"
    entry = parse_upload("bank.csv", "text/csv", csv_bytes)
    ref = store_upload(entry)

    # Call import_transactions with NO mapping — should trigger "Map a column to 'date'" error
    result = _call_tool(
        "import_transactions",
        attachment_ref=ref,
        account_id=account["id"],
        # deliberately omit 'mapping'
    )
    assert result["ok"] is False
    error = result["error"]
    # No-mapping CSV now routes to the schema-inference tier; with the AI reader down the
    # error must still be actionable and point at the manual-mapping fallback.
    assert "map the columns manually" in error or "Map a column" in error
    # The fallback hint (originally "FIX D": diff_statement → bulk_add_transactions →
    # verify_statement_balance). bc66197c routed every format through the one decision
    # pipeline and rewrote this hint to match: the next step is now a `mapping` or a
    # `statement-format:` skill, and hand-entering rows is explicitly the LAST resort rather
    # than the recommended recovery. Assert the current contract — that the error still names
    # an actionable next step and still steers away from hand-parsing.
    assert "statement-format:" in error or "mapping" in error
    assert "last resort" in error


def test_import_wrong_column_name_includes_fallback_hint(account):
    """'Mapped column X is not in the file' must be RAISED, and carry the fallback hint.

    Regression (2026-07-19): this silently returned ok:true. bc66197c made
    `profiles.learn_from_manual_mapping` the only consumer of a caller's mapping, and its
    schema builder drops an unfindable column instead of refusing — so a typo'd
    `description` parsed clean, the row fell back to the placeholder "Imported transaction",
    and that placeholder is hashed into external_ref, so a corrected re-import duplicated
    every row rather than L0-deduping. `decisions.decision_preview` now validates the
    mapping (via the previously-unreachable `import_csv.validate_mapping`) before learning.
    """
    from backend.api.upload import parse_upload, store_upload

    csv_bytes = b"Date,Memo,Amount\n2026-01-02,Coffee,-4.50\n"
    entry = parse_upload("bank.csv", "text/csv", csv_bytes)
    ref = store_upload(entry)

    # Mapping references a non-existent column
    result = _call_tool(
        "import_transactions",
        attachment_ref=ref,
        account_id=account["id"],
        mapping={"date": "Date", "description": "NoSuchColumn", "amount": "Amount"},
    )
    assert result["ok"] is False, "a mapping naming a column the file lacks must be refused"
    error = result["error"]
    # Names the offending column AND the real ones, so the caller can fix it without a probe.
    assert "NoSuchColumn" in error
    assert "Memo" in error, "the error must list the file's actual columns"
    assert "last resort" in error
