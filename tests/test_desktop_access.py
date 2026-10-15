"""Watched folders persist reviewable attachments and never book a transaction."""

from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.desktop_api import router
from backend.api.desktop_transport import SESSION_HEADER, DesktopSession, DesktopTransportMiddleware
from backend.core.errors import ConflictError, ValidationError
from backend.db.desktop_store import DesktopGrantStore, DesktopIngestStore
from backend.db.transaction_store import TransactionStore
from backend.services import desktop_access
from backend.services.attachments import get_attachment_meta

GRANT = "folder-test-1234"
CSV = b"Date,Description,Amount\n2026-09-30,Synthetic shop,-12.34\n"


@pytest.fixture(autouse=True)
def isolated(db_isolated, monkeypatch, tmp_path):
    import backend.db.attachment_store as attachments
    from backend.services import attachments as svc

    monkeypatch.setattr(attachments, "ATTACHMENTS_DIR", tmp_path / "files")
    svc._cache_drop()
    yield
    svc._cache_drop()


def test_grant_store_crud_and_job_content_queries():
    grants = DesktopGrantStore()
    grants.insert({"id": GRANT, "enabled": True})
    assert grants.find_by_id(GRANT)["enabled"] is True
    grants.update_by_id(GRANT, {"enabled": False})
    assert grants.query(enabled=False)[0]["id"] == GRANT
    jobs = DesktopIngestStore()
    jobs.insert({"id": "job-1", "grant_id": GRANT, "content_hash": "hash"})
    assert jobs.find_content(GRANT, "hash")["id"] == "job-1"
    assert jobs.find_content("another", "hash") is None
    assert jobs.recent()[0]["id"] == "job-1"
    grants.delete(GRANT)
    assert grants.find_by_id(GRANT) is None


def test_import_is_durable_hash_deduplicated_attachment_without_money():
    desktop_access.register_folder(GRANT, "Statements")
    result = desktop_access.ingest_file(GRANT, "statement.csv", "text/csv", CSV)
    assert result["duplicate"] is False
    job = result["job"]
    assert job["status"] == "ready"
    assert job["content_hash"] == hashlib.sha256(CSV).hexdigest()
    assert get_attachment_meta(job["attachment_id"])["source_channel"] == "desktop:folder"
    repeated = desktop_access.ingest_file(GRANT, "renamed.csv", "text/csv", CSV)
    assert repeated["duplicate"] is True
    assert repeated["job"]["attachment_id"] == job["attachment_id"]
    assert DesktopIngestStore().count() == 1
    from backend.db.engine import dispose_engine

    dispose_engine()
    assert desktop_access.list_imports()["jobs"] == [job]
    assert TransactionStore().all() == []
    assert desktop_access.mark_reviewed(job["id"])["job"]["status"] == "reviewed"
    assert desktop_access.ingest_file(GRANT, "statement.csv", "text/csv", CSV)["duplicate"] is True


def test_revoked_grants_refuse_intake_and_cannot_be_reenabled():
    desktop_access.register_folder(GRANT, "Statements")
    assert desktop_access.revoke_folder(GRANT) == {"ok": True}
    with pytest.raises(ValidationError, match="revoked"):
        desktop_access.ingest_file(GRANT, "statement.csv", "text/csv", CSV)
    with pytest.raises(ConflictError, match="revoked"):
        desktop_access.register_folder(GRANT, "Statements")
    assert DesktopIngestStore().count() == 0


def test_failed_intake_is_sanitized_and_does_not_retry_each_watch_scan():
    desktop_access.register_folder(GRANT, "Statements")
    result = desktop_access.ingest_file(GRANT, "statement.xyz", "application/octet-stream", b"bad")
    assert result["job"]["status"] == "failed"
    assert "unsupported" in result["job"]["error"]
    assert desktop_access.ingest_file(GRANT, "statement.xyz", "application/octet-stream", b"bad")[
        "duplicate"
    ]


def test_crash_between_attachment_and_completion_recovers_same_attachment(monkeypatch):
    desktop_access.register_folder(GRANT, "Statements")
    original = DesktopIngestStore.update_by_id

    def crash(*args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(DesktopIngestStore, "update_by_id", crash)
    with pytest.raises(RuntimeError, match="simulated"):
        desktop_access.ingest_file(GRANT, "statement.csv", "text/csv", CSV)
    from backend.db.attachment_store import AttachmentStore

    first_id = AttachmentStore().all()[0]["id"]
    monkeypatch.setattr(DesktopIngestStore, "update_by_id", original)
    result = desktop_access.ingest_file(GRANT, "statement.csv", "text/csv", CSV)
    assert result["job"]["attachment_id"] == first_id
    assert len(AttachmentStore().all()) == 1


def test_import_history_is_bounded(monkeypatch):
    desktop_access.register_folder(GRANT, "Statements")
    monkeypatch.setattr(desktop_access, "MAX_INGEST_JOBS", 1)
    desktop_access.ingest_file(GRANT, "statement.csv", "text/csv", CSV)
    with pytest.raises(ConflictError, match="full"):
        desktop_access.ingest_file(GRANT, "statement.csv", "text/csv", CSV + b"new\n")


@pytest.mark.parametrize("filename", ["../statement.csv", "C:\\private\\statement.csv", ""])
def test_filename_cannot_supply_host_path(filename):
    desktop_access.register_folder(GRANT, "Statements")
    with pytest.raises(ValidationError, match="filename"):
        desktop_access.ingest_file(GRANT, filename, "text/csv", CSV)


def test_desktop_api_requires_transport_and_smokes_all_routes():
    browser_app = FastAPI()
    browser_app.include_router(router)
    browser = TestClient(browser_app)
    assert browser.get("/api/desktop/folders").status_code == 403
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(
        DesktopTransportMiddleware, session=DesktopSession("x" * 48, "desktop-test-instance", 8097)
    )
    client = TestClient(app, base_url="http://127.0.0.1:8097", headers={SESSION_HEADER: "x" * 48})
    assert client.get("/api/desktop/status").json()["protocol"] == 1
    assert (
        client.post("/api/desktop/folders", json={"id": GRANT, "label": "Statements"}).status_code
        == 200
    )
    assert client.get("/api/desktop/folders").json()["folders"][0]["id"] == GRANT
    response = client.post(
        "/api/desktop/imports",
        data={"grant_id": GRANT},
        files={"file": ("statement.csv", CSV, "text/csv")},
    )
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "ready"
    assert client.get("/api/desktop/imports").json()["jobs"][0]["attachment_id"]
    job_id = response.json()["job"]["id"]
    assert (
        client.patch(f"/api/desktop/imports/{job_id}", json={"status": "reviewed"}).json()["job"][
            "status"
        ]
        == "reviewed"
    )
    assert client.delete(f"/api/desktop/folders/{GRANT}").json() == {"ok": True}
    assert (
        client.post(
            "/api/desktop/imports",
            data={"grant_id": GRANT},
            files={"file": ("statement.csv", CSV, "text/csv")},
        ).status_code
        == 422
    )
