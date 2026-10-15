"""Explicit folder grants and restart-safe attachment intake, always reviewed before posting."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import datetime, timezone

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.db.attachment_store import AttachmentStore
from backend.db.desktop_store import DesktopGrantStore, DesktopIngestStore
from backend.services.attachment_ingress import persist_inbound_file

_lock = threading.Lock()
_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")
MAX_INGEST_JOBS = 1000
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_folder(grant_id: str, label: str) -> dict:
    if not _ID.fullmatch(grant_id) or not label.strip() or len(label) > 120:
        raise ValidationError("invalid folder grant")
    with _lock:
        store = DesktopGrantStore()
        existing = store.find_by_id(grant_id)
        if existing:
            if not existing["enabled"]:
                raise ConflictError(
                    "revoked folder grants cannot be reused; choose the folder again"
                )
            return existing
        return store.insert(
            {
                "id": grant_id,
                "label": label.strip(),
                "enabled": True,
                "created_at": _now(),
                "revoked_at": None,
            }
        )


def list_folders() -> dict:
    return {"folders": DesktopGrantStore().all()}


def revoke_folder(grant_id: str) -> dict:
    with _lock:
        row = DesktopGrantStore().update_by_id(grant_id, {"enabled": False, "revoked_at": _now()})
        if row is None:
            raise NotFoundError("folder grant not found")
    return {"ok": True}


def list_imports() -> dict:
    return {"jobs": DesktopIngestStore().recent()}


def mark_reviewed(job_id: str) -> dict:
    with _lock:
        store = DesktopIngestStore()
        job = store.find_by_id(job_id)
        if job is None:
            raise NotFoundError("desktop import not found")
        if job["status"] == "reviewed":
            return {"job": job}
        if job["status"] != "ready":
            raise ConflictError("only ready imports can be marked reviewed")
        updated = store.update_by_id(job_id, {"status": "reviewed", "reviewed_at": _now()})
        return {"job": updated}


def _persist_job(store: DesktopIngestStore, job: dict, mime: str, content: bytes) -> dict:
    # A crash after attachment persistence but before job completion must not duplicate it.
    attached = AttachmentStore().query(source_channel="desktop:folder", source_message_id=job["id"])
    try:
        if attached:
            attachment_id = attached[0]["id"]
        else:
            result = persist_inbound_file(
                job["filename"],
                mime,
                content,
                source_channel="desktop:folder",
                source_message_id=job["id"],
            )
            attachment_id = result["attachment_id"]
        changes = {"status": "ready", "attachment_id": attachment_id, "error": None}
    except ValueError:
        changes = {
            "status": "failed",
            "attachment_id": None,
            "error": "File could not be imported. It may be encrypted or unsupported.",
        }
        log.info("desktop file intake refused for job %s", job["id"])
    changes["completed_at"] = _now()
    return store.update_by_id(job["id"], changes)


def ingest_file(grant_id: str, filename: str, mime: str, content: bytes) -> dict:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationError("file too large")
    if not filename or len(filename) > 255 or "/" in filename or "\\" in filename:
        raise ValidationError("invalid filename")
    content_hash = hashlib.sha256(content).hexdigest()
    # The host owns path checks. Here revocation and intake serialize, so a completed revoke
    # cannot be followed by a file persistence from a previously authorized request.
    with _lock:
        grant = DesktopGrantStore().find_by_id(grant_id)
        if not grant or not grant["enabled"]:
            raise ValidationError("folder access has been revoked or was never granted")
        store = DesktopIngestStore()
        job = store.find_content(grant_id, content_hash)
        if job and job["status"] != "pending":
            return {"job": job, "duplicate": True}
        if job is None:
            if store.count() >= MAX_INGEST_JOBS:
                raise ConflictError("desktop import history is full; import files manually")
            job = store.insert(
                {
                    "id": new_id("djob"),
                    "grant_id": grant_id,
                    "content_hash": content_hash,
                    "filename": filename,
                    "status": "pending",
                    "attachment_id": None,
                    "created_at": _now(),
                    "completed_at": None,
                    "error": None,
                }
            )
        return {"job": _persist_job(store, job, mime, content), "duplicate": False}
