"""Webhook notification channel (generic, vendor-neutral push).

Registers an async ``_send`` on the ``core.channels`` seam so ``services.notify`` can
fan a message out to an arbitrary HTTP endpoint WITHOUT ``services`` importing
``connectors`` (the same downward ``connectors → core`` inversion as Telegram /
email-out — import-linter Contract 1/4 stays green). The POST uses stdlib
``urllib.request`` (no new dependency); the body is ``{"text": message}`` JSON.

This is the universal building block for Slack/Discord/Mattermost incoming webhooks,
n8n/Zapier hooks, a home-automation endpoint, etc. — a per-vendor adapter (a Slack
``blocks`` payload, a WhatsApp Business send) is deferred.

Config lives in ``ChannelConfigStore`` (``db/``): ``{url, secret_header_name, secret,
enabled}`` — the URL and the secret are both connector secrets. When a secret is set it is
sent as the configured header (default ``X-Webhook-Secret``) so the receiver can verify the
caller. The channel is registered only when **enabled AND configured** (``is_active()``).

Error isolation: the blocking HTTP call runs in a thread and any failure (timeout,
non-2xx, bad URL) is **caught** and returned as ``{"error": ...}`` — a channel ``_send``
never raises into the Notifier. The secret is never logged.
"""

from __future__ import annotations

import json
import logging
import urllib.error

from backend.core.channels import register_channel
from backend.core.pinned_http import pinned_urlopen

logger = logging.getLogger(__name__)

CHANNEL_NAME = "webhook"


def _send_sync(cfg: dict, message: str) -> dict:
    """Blocking HTTP POST (run in a thread by ``_send``). POSTs ``{"text": message}``."""
    url = cfg["url"]
    payload = json.dumps({"text": message}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    secret = (cfg.get("secret") or "").strip()
    if secret:
        header_name = (cfg.get("secret_header_name") or "X-Webhook-Secret").strip()
        headers[header_name] = secret

    # Owner-typed URL: a LAN receiver (Home Assistant, ntfy) is fine on a self-hosted box,
    # but every hop is resolved, validated and pinned, and a hosted instance gets public only.
    with pinned_urlopen(
        url, headers=headers, data=payload, timeout=15, owner_configured=True
    ) as resp:
        status = getattr(resp, "status", None) or resp.getcode()
    return {"sent": 1, "status": status}


async def _send(message: str) -> dict:
    """POST ``message`` to the configured webhook URL. Returns ``{sent: 1, status: <code>}``.

    Best-effort: on a config / HTTP error this returns ``{"error": "..."}`` rather than
    raising, so the Notifier's other channels still deliver. The secret is never logged.
    """
    import asyncio

    from backend.db import ChannelConfigStore

    # Gate the send on *configured* (has a URL), not *enabled* — so the /test endpoint can
    # verify a configured-but-not-yet-enabled channel. Registration on the seam is still
    # gated on enabled+configured via register_if_active().
    cfg = ChannelConfigStore().get()
    if not cfg["url"]:
        return {"error": "webhook channel not configured"}
    try:
        return await asyncio.to_thread(_send_sync, cfg, message)
    except urllib.error.HTTPError as exc:  # non-2xx — report the status, not the body
        logger.warning("webhook channel POST failed: HTTP %s", exc.code)
        return {"error": f"HTTP {exc.code}"}
    except Exception as exc:  # never propagate — log without the secret
        logger.warning("webhook channel POST failed: %s", exc)
        return {"error": str(exc)}


def is_active() -> bool:
    """Whether the webhook is enabled and has the URL it needs — the ONE predicate for both
    registering the channel and reporting it as available."""
    from backend.db import ChannelConfigStore

    return ChannelConfigStore().is_active()


def register_if_active() -> bool:
    """Register the webhook channel on the seam iff it is enabled + configured.

    Called from ``main.py``'s lifespan (and by ``/api/channels`` after a config change).
    Returns whether it was registered. Idempotent — last registration under ``"webhook"``
    wins.
    """
    if is_active():
        register_channel(CHANNEL_NAME, _send)
        return True
    return False
