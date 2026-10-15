"""Email connector control — the one settings save, a test-connection and a test-send.

Email is a generic bidirectional connector (RFC docs/rfc/email-as-generic-source.md), NOT an
auto-logging pipeline: ONE account (address + password) serves IMAP receive, SMTP send and the
email alert channel, and it is saved in ONE request. What happens with fetched emails is
entirely up to whatever Automations the user configures.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import field_validator

from backend.db import EmailConfigStore
from backend.models.base import ONBaseModel, _blank_to_none
from backend.services import connectors

router = APIRouter()


class EmailConfigPatch(ONBaseModel):
    """The whole flat Email form. Every field optional (``None`` = leave unchanged)."""

    enabled: Optional[bool] = None
    provider: Optional[str] = None  # "gmail"/"outlook"/"icloud"/"fastmail"/"custom"
    address: Optional[str] = None  # IMAP login, SMTP login, From and alert recipient
    password: Optional[str] = None  # WRITE-ONLY — blank leaves the saved one unchanged
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_folder: Optional[str] = None
    lookback_days: Optional[int] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    use_tls: Optional[bool] = None

    # A blank string is "unset", so a half-filled form never clobbers a saved value.
    _blank_strs = field_validator(
        "provider", "address", "imap_host", "imap_folder", "smtp_host", mode="before"
    )(_blank_to_none)


@router.get("/api/email/config")
def get_email_config():
    return EmailConfigStore().public()


@router.put("/api/email/config")
def update_email_config(body: EmailConfigPatch):
    changes = body.model_dump(exclude_none=True)
    return connectors.save_email_config(changes) | {"_applied": list(changes.keys())}


@router.get("/api/email/status")
def get_email_status():
    cfg = EmailConfigStore().public()
    return {
        "configured": cfg["configured"],
        "enabled": cfg["enabled"],
        "last_status": cfg["last_status"],
        "last_error": cfg["last_error"],
    }


@router.post("/api/email/test-connection")
def test_email_connection():
    """Verify the saved IMAP credentials by connecting and authenticating.
    Returns {"ok": true} on success or {"ok": false, "error": "..."} on failure.
    No messages are read and no cursor is advanced."""
    from backend.ingest.imap_client import test_connection

    creds = EmailConfigStore().credentials(require_enabled=False)
    if creds is None:
        return {"ok": False, "error": "IMAP credentials not fully configured."}
    ok, err = test_connection(creds)
    return {"ok": ok} if ok else {"ok": False, "error": err}


@router.post("/api/email/test-send")
async def test_email_send():
    """Send a test email to the connector's own address through the saved SMTP settings.

    Goes straight through the adapter, so a saved-but-paused connector can be verified. The
    adapter returns ``{"error": ...}`` rather than raising, so the UI can show why; only a
    connector with no SMTP settings saved yet is a 400.
    """
    from backend.connectors.email_out.channel import _send

    if EmailConfigStore().smtp_credentials(require_enabled=False) is None:
        raise HTTPException(400, "email is not configured")
    result = await _send("Open Net Worth test notification — your channel is working.")
    return {"name": "email", "result": result}
