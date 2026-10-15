"""Which address to hand a phone: the one the request came in on, unless that is this machine.

The desktop app and a browser on the same computer reach the instance at ``localhost``, which
a phone can never open — a QR for it is a trap. The address a phone CAN use (a Cloudflare
tunnel, a Tailscale name) only shows up when someone reaches the instance through it, or types
it into Settings; either way it is remembered and suggested from then on.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from backend.db.phone_shortcut_state_store import PhoneShortcutStateStore

_LOOPBACK_HOSTS = {"localhost", "0.0.0.0", "::1"}


def is_own_computer(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def remember(url: str) -> None:
    """Keep ``url`` as the phone's address when a phone could actually use it."""
    if url and not is_own_computer(url):
        store = PhoneShortcutStateStore()
        if store.phone_base_url() != url:
            store.set_phone_base_url(url)


def suggest(detected: str) -> str:
    """The address to pre-fill: ``detected`` if a phone can use it, else the remembered one."""
    if not is_own_computer(detected):
        remember(detected)
        return detected
    return PhoneShortcutStateStore().phone_base_url() or detected
