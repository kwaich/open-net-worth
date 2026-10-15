"""Native statement review retains attachment identity and current Private policy."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import import_api, statement_parse_api
from backend.core.errors import NotFoundError, ValidationError
from backend.db.statement_store import StatementStore
from backend.services import attachments, vault_changes
from backend.services.attachment_ingress import persist_inbound_file
from backend.services.statements import decisions, parse_jobs, source_guard

CSV = b"Date,Description,Amount\n2026-09-30,Synthetic shop,-12.34\n"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture(autouse=True)
def isolated(db_isolated, monkeypatch, tmp_path):
    from backend.db import attachment_store, statement_store

    monkeypatch.setattr(attachment_store, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(statement_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(statement_store, "statements_files_dir", lambda: tmp_path / "statements")
    attachments._cache_drop()
    yield
    attachments._cache_drop()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(import_api.router)
    app.include_router(statement_parse_api.router)
    return TestClient(app, headers=HEADERS)


def original():
    return persist_inbound_file("original.csv", "text/csv", CSV, source_channel="desktop:folder")[
        "attachment_id"
    ]


def never(*args, **kwargs):
    pytest.fail("Private or substituted bytes reached parsing/model work")


@pytest.mark.parametrize("private", [False, True])
def test_uploaded_bytes_must_match_original_attachment(client, monkeypatch, private):
    ref = original()
    if private:
        vault_changes.keep_attachment(ref, private=True)
    monkeypatch.setattr(decisions, "parse_statement_bytes", never)
    response = client.post(
        "/api/statements/preview",
        files={"file": ("substitute.csv", CSV + b"2026-09-30,Substitute,900\n", "text/csv")},
        data={"source_attachment_id": ref},
    )
    assert response.status_code == 422
    assert ("marked Private" if private else "do not match") in response.json()["detail"]
    assert StatementStore().all() == []


@pytest.mark.parametrize("data", [{}, {"mode": "async"}, {"columns_only": "true"}])
def test_private_file_never_reaches_preview_or_header_model(client, monkeypatch, data):
    ref = original()
    vault_changes.keep_attachment(ref, private=True)
    monkeypatch.setattr(decisions, "parse_statement_bytes", never)
    monkeypatch.setattr(import_api.import_csv, "suggest_mapping", never)
    monkeypatch.setattr(parse_jobs, "schedule", never)
    response = client.post(
        "/api/statements/preview",
        files={"file": ("original.csv", CSV, "text/csv")},
        data={"source_attachment_id": ref, **data},
    )
    assert response.status_code == 422
    assert "marked Private" in response.json()["detail"]
    assert StatementStore().all() == []


def test_public_preview_preserves_provenance(client, monkeypatch):
    ref = original()
    seen = {}

    def preview(content, fmt, account_id, **kwargs):
        seen.update(kwargs)
        record = StatementStore().create(
            "original.csv",
            "text/csv",
            content,
            {},
            source_attachment_id=kwargs["source_attachment_id"],
        )
        return {"statement_id": record["id"]}

    monkeypatch.setattr(decisions, "decision_preview", preview)
    response = client.post(
        "/api/statements/preview",
        files={"file": ("original.csv", CSV, "text/csv")},
        data={"source_attachment_id": ref},
    )
    assert response.status_code == 200
    assert seen["source_attachment_id"] == ref
    assert (
        StatementStore().find_by_id(response.json()["statement_id"])["source_attachment_id"] == ref
    )


def test_async_job_keeps_provenance_and_rechecks_later_privacy(client, monkeypatch):
    ref = original()
    queued = {}

    def schedule(statement_id, fn, *args, **kwargs):
        queued.update({"fn": fn, "args": args, "kwargs": kwargs})
        return True

    monkeypatch.setattr(parse_jobs, "schedule", schedule)
    response = client.post(
        "/api/statements/preview",
        files={"file": ("original.csv", CSV, "text/csv")},
        data={"source_attachment_id": ref, "mode": "async"},
    )
    assert response.status_code == 202
    assert queued["kwargs"]["source_attachment_id"] == ref
    assert (
        StatementStore().find_by_id(response.json()["statement_id"])["source_attachment_id"] == ref
    )
    vault_changes.keep_attachment(ref, private=True)
    monkeypatch.setattr(decisions, "parse_statement_bytes", never)
    with pytest.raises(ValidationError, match="marked Private"):
        fmt, *rest = queued["args"]
        queued["fn"](fmt, CSV, *rest, **queued["kwargs"])


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_reopening_statement_honors_original_private_attachment(client, monkeypatch, mode):
    ref = original()
    record = StatementStore().create("original.csv", "text/csv", CSV, {}, source_attachment_id=ref)
    vault_changes.keep_attachment(ref, private=True)
    monkeypatch.setattr(decisions, "parse_statement_bytes", never)
    monkeypatch.setattr(parse_jobs, "schedule", never)
    response = client.post(f"/api/statements/{record['id']}/preview", data={"mode": mode})
    assert response.status_code == 422
    assert "marked Private" in response.json()["detail"]


def test_cached_statement_provenance_cannot_be_omitted_to_bypass_private():
    ref = original()
    StatementStore().create("original.csv", "text/csv", CSV, {}, source_attachment_id=ref)
    vault_changes.keep_attachment(ref, private=True)
    with pytest.raises(ValidationError, match="marked Private"):
        decisions.decision_preview(CSV, "csv", None)


def test_missing_original_attachment_fails_closed():
    with pytest.raises(NotFoundError, match="no longer available"):
        source_guard.validate_preview_source(CSV, "missing-attachment")
    ref = original()
    attachments.delete_attachment(ref)
    with pytest.raises(NotFoundError, match="no longer available"):
        decisions.decision_preview(CSV, "csv", None, source_attachment_id=ref)
    assert StatementStore().all() == []


def test_authorized_normalized_import_bytes_are_accepted(monkeypatch):
    ref = original()
    monkeypatch.setattr(attachments, "get_attachment_for_model", lambda _: {"content": b"clean"})
    source_guard.validate_preview_source(b"clean", ref)


def test_url_cannot_claim_attachment_identity(client, monkeypatch):
    monkeypatch.setattr(import_api.import_common, "fetch_url_bytes", never)
    response = client.post(
        "/api/statements/preview",
        data={"source_url": "https://example.com/file.csv", "source_attachment_id": original()},
    )
    assert response.status_code == 422
    assert "requires an uploaded file" in response.json()["detail"]
