"""Full-data disaster-recovery backup API — settings + history + on-demand run.

Endpoints:
  GET  /api/backup/settings   — read backup_enabled + retention_days
  PUT  /api/backup/settings   — set backup_enabled and/or retention_days
  GET  /api/backup            — list existing backups (newest first)
  POST /api/backup/run-now    — trigger an immediate backup (bypasses the enabled toggle)
  GET  /api/backup/{name}/download — stream one backup zip
  POST /api/backup/{name}/restore  — replace all data with one backup (safety backup first)
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.api._deps import _bearer_token
from backend.db.app_config_store import BACKUP_ENABLED_FLAG, AppConfigStore
from backend.services import backup as backup_service

router = APIRouter()


class BackupSettingsBody(BaseModel):
    enabled: Optional[bool] = None
    retention_days: Optional[int] = None


@router.get("/api/backup/settings")
def get_backup_settings():
    cfg = AppConfigStore()
    return {
        "enabled": cfg.get_flag(BACKUP_ENABLED_FLAG, default=True),
        "retention_days": cfg.get_backup_retention_days(),
    }


@router.put("/api/backup/settings")
def put_backup_settings(body: BackupSettingsBody):
    cfg = AppConfigStore()
    if body.retention_days is not None and body.retention_days < 1:
        raise HTTPException(status_code=422, detail="retention_days must be at least 1")
    if body.enabled is not None:
        cfg.set_flag(BACKUP_ENABLED_FLAG, body.enabled)
    if body.retention_days is not None:
        cfg.set_backup_retention_days(body.retention_days)
    return {
        "enabled": cfg.get_flag(BACKUP_ENABLED_FLAG, default=True),
        "retention_days": cfg.get_backup_retention_days(),
    }


@router.get("/api/backup")
def list_backups():
    backups = backup_service.list_backups()
    return {
        "backups": backups,
        "last_backup_at": backups[0]["created_at"] if backups else None,
    }


@router.post("/api/backup/run-now")
def run_backup_now():
    result = backup_service.run_backup(force=True, trigger="manual")
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error") or "backup failed")
    return result


def _require_xhr(request: Request) -> None:
    """CSRF defence-in-depth, identical to ``GET /api/data/export`` (RFC P3-AUTH §10).

    The session cookie is ``SameSite=Lax`` — sent on a top-level cross-site GET — and
    ``AUTH_TRUST_LOOPBACK`` defaults TRUE, so on a local install the auth gate returns early
    for any request from 127.0.0.1. Without this, a form POST from ANY page the operator has
    open (a CORS *simple* request: no preflight, browser sends it) reaches
    ``/api/backup/{name}/restore`` and rolls their data back. The attacker cannot read the
    response — the side effect IS the attack.

    A Bearer token or a custom header both prove the request came from a real client rather
    than a cross-origin navigation or form post.
    """
    has_bearer = _bearer_token(request) is not None
    requested_with = request.headers.get("x-requested-with", "").strip().lower()
    if not has_bearer and requested_with != "xmlhttprequest":
        raise HTTPException(403, "missing X-Requested-With header")


@router.get("/api/backup/{name}/download")
def download_backup(request: Request, name: str, background: BackgroundTasks):
    """Stream one backup zip, with every credential-bearing file stripped.

    `name` is resolved by the service, which accepts nothing but a literal backup timestamp,
    so a traversal attempt never reaches the filesystem. The archive is re-zipped rather than
    streamed as-is: a DR snapshot on disk contains auth.json and every connector secret, and
    an HTTP download must not be an exfiltration primitive for them.
    """
    _require_xhr(request)
    if backup_service.resolve_backup(name) is None:
        raise HTTPException(status_code=404, detail="unknown backup")

    tmp = Path(tempfile.mkdtemp(prefix="onw-backup-dl-")) / f"open-net-worth-backup-{name}.zip"
    result = backup_service.write_sanitised_copy(name, tmp)
    if not result.get("ok"):
        shutil.rmtree(tmp.parent, ignore_errors=True)
        raise HTTPException(status_code=500, detail=result.get("error") or "download failed")

    background.add_task(shutil.rmtree, tmp.parent, ignore_errors=True)
    return FileResponse(
        tmp,
        media_type="application/zip",
        filename=tmp.name,
        background=background,
        # Names what was left out, so "my download is missing auth.json" is answerable
        # without reading the source.
        headers={"X-Onw-Stripped": ",".join(result.get("stripped") or []) or "none"},
    )


@router.post("/api/backup/{name}/restore")
def restore_backup(request: Request, name: str):
    """Replace all current data with one backup's contents.

    Destructive, so it writes a safety backup first and reports its name — the UI tells the
    user a restore can itself be undone, and this is what makes that true. Credentials and the
    still-file-backed non-restorable datasets (single-use nonce queues, chat pairing) are never
    rolled back; see ``core.sensitive``.

    The guarantee spans BOTH storage tiers: ``core.sensitive.NON_RESTORABLE_FILES`` (still-file
    datasets) AND ``NON_RESTORABLE_TABLES`` — the tool-grant table, the audit trail, the MCP
    write-log and the automation-runtime queue, which moved into ``onw.db`` (the JSON→DB storage
    migration). ``restore_backup`` captures those tables' current rows and re-applies them across
    the ``onw.db`` swap, so a restore never un-revokes a grant or rolls back the audit trail. A
    restore whose archive predates the DB (no ``onw.db`` member, so nothing to swap in) is refused
    rather than allowed to delete the live database.
    """
    _require_xhr(request)
    result = backup_service.restore_backup(name)
    if not result.get("ok"):
        error = result.get("error") or "restore failed"
        raise HTTPException(status_code=404 if error == "unknown backup" else 500, detail=error)
    return result
