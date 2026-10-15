"""Outbound notification-channel config — REST list / configure / test (webhook).

Thin router (the dependency rule): validate the request → call ``ChannelConfigStore`` /
the channel adapter → shape the response. The actual transport (the HTTP POST) lives in the
``connectors/`` adapter; the financial app does not format money here (a notification body
is produced by whatever caller fans out via the Notifier). Email's alert channel belongs to
the Email connector (``api/email_ingest_api.py``).

Channel config is **human-only** (like auth / email-ingest / mcp-clients): configuring an
outbound channel with stored credentials is instance-security plumbing, not a Lucius
action. Lucius already SENDS via the ``notify`` tool / Notifier fan-out — that is unchanged
and orthogonal to this config surface.

All routes are gated by the app-wide ``require_auth`` dependency (mounted in ``main.py``).
The write-only URL and secret are NEVER returned — every response is the store's
``public()`` shape (``has_*`` flags and the URL's host only).

On a config change (PUT) the channel is re-registered/unregistered on the ``core.channels``
seam so the Notifier picks up the new state live (mirrors ``reschedule_source``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.core.channels import registered_channels, unregister_channel
from backend.db import ChannelConfigStore
from backend.models.channel_config import WebhookChannelPatch

router = APIRouter(tags=["channels"])

_CHANNEL = "webhook"


def _reconcile_seam() -> bool:
    """Register the webhook channel on the seam iff active, else unregister it. Returns active.

    Imported lazily so this router — at the api tier — does not pull the connectors in at
    module import; the edge is still ``api → connectors → core`` (down).
    """
    from backend.connectors.webhook.channel import register_if_active

    active = register_if_active()
    if not active:
        unregister_channel(_CHANNEL)
    return active


@router.get("/api/channels")
def list_channels():
    """All managed outbound channels: config (secrets stripped) + configured/active state."""
    store = ChannelConfigStore()
    return {"channels": [{**cfg, "active": store.is_active()} for cfg in store.public_all()]}


@router.get("/api/notify-channels")
def list_notify_channels():
    """The notification channels currently LIVE on the fan-out seam — i.e. the actual
    targets a ``notify`` task reaches when no explicit allowlist is set.

    Distinct from ``/api/channels`` (which lists only the managed-config channel:
    webhook). Here ``in_app`` is always present; ``telegram`` appears only
    when that connector is booted, and the managed ones only when configured + active.
    Backs the scheduled-task editor's "Send to" picker so the UI offers exactly the
    channels a notification can land on. Read-only, never 500s."""
    return {"channels": sorted(registered_channels().keys())}


@router.put("/api/channels/{name}")
def update_channel(name: str, body: dict):
    """Configure/enable the webhook. A blank URL or secret leaves the saved one unchanged
    (write-only).

    The body is validated against the patch model so unknown keys are dropped and a
    half-filled form never 422s the whole save.
    """
    if name != _CHANNEL:
        raise HTTPException(404, f"unknown channel: {name}")
    changes = WebhookChannelPatch.model_validate(body).model_dump(exclude_none=True)
    store = ChannelConfigStore()
    store.update(changes)
    active = _reconcile_seam()
    return {**store.public(), "active": active, "_applied": list(changes.keys())}


@router.post("/api/channels/{name}/test")
async def test_channel(name: str):
    """Send a test notification through the webhook; report the result.

    Sends directly via the adapter (a configured-but-not-yet-enabled channel can be
    verified). Never raises on a send failure — the adapter returns ``{"error": ...}`` so
    the UI can show why. A 404 is only for an unknown channel name; a 400 for one that is
    not yet configured (no URL).
    """
    if name != _CHANNEL:
        raise HTTPException(404, f"unknown channel: {name}")
    if not ChannelConfigStore().is_configured():
        raise HTTPException(400, f"{name} channel is not configured")

    from backend.connectors.webhook.channel import _send

    result = await _send("Open Net Worth test notification — your channel is working.")
    return {"name": name, "result": result}
