"""The user's clock — one source of truth for "today" and "what time is it for them".

A hosted instance runs in a container whose clock is UTC, so the server's local time says nothing
about the owner's. Everything that decides a user-facing day or time of day (date defaults,
greetings, the turn context, month boundaries) reads it here, never from ``date.today()`` /
``datetime.now()``. The zone is an IANA name stored with the profile; ``services.profile`` registers
the provider (``core`` cannot import it). Unset → the server's own local zone, so a self-hosted or
desktop install, where the server IS the user's machine, behaves exactly as before.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, tzinfo
from typing import Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_log = logging.getLogger(__name__)
_provider: Optional[Callable[[], Optional[str]]] = None


def register_zone_provider(provider: Optional[Callable[[], Optional[str]]]) -> None:
    """Install the callable that returns the stored IANA zone name (or None when unset)."""
    global _provider
    _provider = provider


def is_valid_zone(name: object) -> bool:
    if not isinstance(name, str) or not name or name != name.strip():
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def user_zone() -> Optional[tzinfo]:
    """The stored zone, or None (= the server's local zone) when unset or no longer resolvable."""
    try:
        name = _provider() if _provider else None
    except Exception:  # noqa: BLE001 — a clock read must never fail (e.g. before the DB is migrated)
        _log.debug("user_time: zone provider failed; using the server zone", exc_info=True)
        name = None
    if not name or not is_valid_zone(name):
        return None
    return ZoneInfo(name)


def user_now() -> datetime:
    """Timezone-aware now in the user's zone (the server's local zone when unset)."""
    zone = user_zone()
    return datetime.now(zone) if zone else datetime.now().astimezone()


def user_today() -> date:
    return user_now().date()
