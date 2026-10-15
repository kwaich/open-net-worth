"""Behavioral tests for the bounded ``list_statements`` AI tool (RFC
cross-channel-attachments §8.2).

Before this change ``list_statements`` had an EMPTY schema — no filters, no pagination — and
dumped every ``StatementStore`` record unbounded. These tests pin: the new account_ids /
date-range / parsed / imported / meta_status / kind filters, stable limit/offset pagination
with a continuation note, and the "no matching statements" vs "no statements exist at all"
zero-result distinction (the same convention ``list_accounts`` already uses).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools.statements import list_statements
from backend.services import ledger as ledger_service


def _insert(sid: str, *, account_id=None, created_at="2026-01-01T00:00:00Z", **overrides) -> None:
    row = {
        "id": sid,
        "created_at": created_at,
        "filename": f"{sid}.pdf",
        "mime": "application/pdf",
        "size_bytes": 10,
        "file_hash": sid,
        "file_path": "",
        "kind": "text",
        "upload_entry": {},
        "parsed_rows": None,
        "parsed_at": None,
        "account_id": account_id,
        "batch_ids": [],
        "import_meta": None,
    }
    row.update(overrides)
    ledger_service.statements.insert(row)


def _call(**kwargs) -> dict:
    payload, _ = asyncio.run(list_statements(kwargs))
    return json.loads(payload)


@pytest.fixture
def env(db_isolated):
    ledger_service.accounts.create(
        {"id": "acc_a", "name": "Account A", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    ledger_service.accounts.create(
        {"id": "acc_b", "name": "Account B", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    _insert(
        "s1",
        account_id="acc_a",
        kind="text",
        parsed_rows=["row"],
        batch_ids=["b1"],
        import_meta={"statement_date": "2026-01-15"},
        created_at="2026-01-16T00:00:00Z",
    )
    _insert(
        "s2",
        account_id="acc_a",
        kind="pdf_pages",
        parsed_rows=None,
        batch_ids=[],
        import_meta={"meta_unavailable": True},
        created_at="2026-02-10T00:00:00Z",
    )
    _insert(
        "s3",
        account_id="acc_b",
        kind="image",
        parsed_rows=["row"],
        batch_ids=[],
        import_meta=None,
        created_at="2026-03-05T00:00:00Z",
    )
    _insert(
        "s4",
        account_id="acc_b",
        kind="text",
        parsed_rows=None,
        batch_ids=["b2"],
        import_meta={"statement_date": "2026-04-20"},
        created_at="2026-04-21T00:00:00Z",
    )
    _insert(
        "s5",
        account_id=None,
        kind="text",
        parsed_rows=None,
        batch_ids=[],
        import_meta=None,
        created_at="2026-05-01T00:00:00Z",
    )
    _insert(
        "s6",
        account_id="acc_a",
        kind="text",
        parsed_rows=["row"],
        batch_ids=["b3"],
        import_meta={"statement_date": "2026-06-01"},
        created_at="2026-06-02T00:00:00Z",
    )


def _ids(result: dict) -> set:
    return {s["id"] for s in result["statements"]}


def test_filters_by_account_id(env):
    result = _call(account_ids=["acc_a"])
    assert _ids(result) == {"s1", "s2", "s6"}
    assert result["total"] == 3


def test_filters_by_account_name(env):
    result = _call(account_ids=["Account B"])
    assert _ids(result) == {"s3", "s4"}


def test_unmatched_account_ref_is_reported_not_dropped(env):
    result = _call(account_ids=["does-not-exist"])
    assert result["total"] == 0
    assert "No account matches" in result["note"]
    assert "does-not-exist" in result["note"]


def test_date_range_filter_uses_statement_own_date(env):
    # s3 has no import_meta -> falls back to its upload date (2026-03-05), s4's printed
    # statement_date is 2026-04-20 -> both land inside this window.
    result = _call(date_from="2026-03-01", date_to="2026-04-30")
    assert _ids(result) == {"s3", "s4"}


def test_parsed_filter(env):
    assert _ids(_call(parsed=True)) == {"s1", "s3", "s6"}
    assert _ids(_call(parsed=False)) == {"s2", "s4", "s5"}


def test_imported_filter(env):
    assert _ids(_call(imported=True)) == {"s1", "s4", "s6"}
    assert _ids(_call(imported=False)) == {"s2", "s3", "s5"}


def test_meta_status_filter(env):
    assert _ids(_call(meta_status="available")) == {"s1", "s4", "s6"}
    assert _ids(_call(meta_status="unavailable")) == {"s2"}
    assert _ids(_call(meta_status="unknown")) == {"s3", "s5"}


def test_kind_filter(env):
    result = _call(kind="image")
    assert _ids(result) == {"s3"}


def test_zero_matches_distinguished_from_zero_statements(env):
    result = _call(kind="image", account_ids=["acc_a"])  # image statement belongs to acc_b
    assert result["total"] == 0
    assert result["showing"] == 0
    assert "0 of 6" in result["note"]
    assert "DO exist" in result["note"]


def test_no_statements_exist_at_all(db_isolated):
    result = _call()
    assert result["total"] == 0
    assert result["statements"] == []
    assert "No statements have been uploaded yet" in result["note"]


def test_get_statement_is_unchanged_by_this_task(env):
    # get_statement stays the bounded single-record detail read — out of scope here.
    from backend.ai.tools.statements import get_statement

    payload, _ = asyncio.run(get_statement({"statement_id": "s1"}))
    result = json.loads(payload)
    assert result["ok"] is True
    assert result["statement"]["id"] == "s1"


def test_pagination_default_limit_and_offset(db_isolated):
    for i in range(23):
        _insert(f"page_{i:02d}", created_at=f"2026-07-{i + 1:02d}T00:00:00Z")

    first = _call()
    assert first["showing"] == 20
    assert first["total"] == 23
    assert "Showing 1-20 of 23" in first["note"]

    rest = _call(offset=20)
    assert rest["showing"] == 3
    assert rest["total"] == 23
    assert "note" not in rest
