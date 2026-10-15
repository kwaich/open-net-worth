"""Where each KIND of Intel notification is sent outside the app (Settings › Notifications).

``config.notify_routes`` maps a kind to the external channel ids it goes to. In-app is never
listed: every kind always lands in the app. A price alert owns its OWN ``channels`` (saved on
the alert); its row here is only the default a NEW alert starts with — nothing reads it at fire
time. ADR 2026-09-30-notification-channels-per-kind-and-per-alert.
"""

from __future__ import annotations

from backend.core.errors import ValidationError

PRICE_ALERTS = "price_alerts"
WATCH_ALERTS = "watch_alerts"
NEWS = "news"
NOTIFY_KINDS = (PRICE_ALERTS, WATCH_ALERTS, NEWS)


def default_routes() -> dict[str, list[str]]:
    return {kind: [] for kind in NOTIFY_KINDS}


def routes_from_config(config: dict | None) -> dict[str, list[str]]:
    stored = (config or {}).get("notify_routes") or {}
    return {kind: list(stored.get(kind) or []) for kind in NOTIFY_KINDS}


def channels_for(kind: str) -> list[str]:
    """The external channels ``kind`` goes to right now. Never raises — a notification path
    must not break on a config read; an unreadable config sends nowhere outside the app."""
    try:
        from backend.services.watchlist import read_watchlist

        return routes_from_config(read_watchlist().get("config"))[kind]
    except Exception:
        return []


def _known_external_ids() -> set[str]:
    from backend.services.connectors import known_channel_ids

    return known_channel_ids() - {"in_app"}


def normalize_channels(channels: object) -> list[str]:
    """A saved channel list: known external ids only, de-duplicated, order kept. An unknown id
    RAISES (never silently dropped) so a caller learns its choice was not stored."""
    if channels is None:
        return []
    if not isinstance(channels, list) or not all(isinstance(c, str) for c in channels):
        raise ValidationError("channels must be a list of channel ids")
    known = _known_external_ids()
    out: list[str] = []
    for raw in channels:
        cid = raw.strip()
        if cid == "in_app" or not cid or cid in out:
            continue
        if cid not in known:
            raise ValidationError(f"unknown channel {cid!r}; known: {sorted(known)}")
        out.append(cid)
    return out


def merge_routes(current: dict | None, patch: object) -> dict[str, list[str]]:
    """Apply a partial ``{kind: [channel ids]}`` patch onto the stored routes."""
    if not isinstance(patch, dict):
        raise ValidationError("notify_routes must be an object of {kind: [channel ids]}")
    merged = routes_from_config({"notify_routes": current or {}})
    for kind, channels in patch.items():
        if kind not in NOTIFY_KINDS:
            raise ValidationError(f"unknown notification kind {kind!r}; known: {NOTIFY_KINDS}")
        merged[kind] = normalize_channels(channels)
    return merged
