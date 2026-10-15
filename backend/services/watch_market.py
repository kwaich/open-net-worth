"""watch_market — the market clock and quote sanity checks the watch rules need. Pure: ``now`` is
passed in, no I/O.

* ``volume_pace`` — today's volume vs what a NORMAL day would have traded by this point of the
  session. Comparing a morning's cumulative volume with a full day's average under-reads every
  morning; pacing it by the elapsed share of the session fixes that.
* ``split_suspect`` — a day move that matches a split ratio on ordinary volume is a split (the
  feed adjusted the price, the quantity hasn't caught up), not a crash or a rally.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

SPLIT_MOVES_PCT = (-50.0, -66.67, -75.0, -80.0, -90.0, 100.0, 200.0, 300.0, 400.0, 900.0)
SPLIT_TOLERANCE_PCT = 3.0
SPLIT_MAX_PACE = 3.0

# Regular-session hours by exchange timezone, for "volume on pace" (local open, close as hours).
SESSION_HOURS: dict[str, tuple[float, float]] = {
    "America/New_York": (9.5, 16.0),
    "America/Toronto": (9.5, 16.0),
    "Europe/London": (8.0, 16.5),
    "Europe/Berlin": (9.0, 17.5),
    "Europe/Paris": (9.0, 17.5),
    "Europe/Amsterdam": (9.0, 17.5),
    "Europe/Zurich": (9.0, 17.5),
    "Asia/Singapore": (9.0, 17.0),
    "Asia/Hong_Kong": (9.5, 16.0),
    "Asia/Tokyo": (9.0, 15.5),
    "Australia/Sydney": (10.0, 16.0),
}


def session_fraction(snap: dict, now: datetime) -> float | None:
    """Share of today's regular session already traded (0–1), or 1.0 outside it; None when the
    exchange's hours aren't known (then volume is only judged against a full day)."""
    state = (snap.get("market_state") or "").upper()
    if state and state != "REGULAR":
        return 1.0
    hours = SESSION_HOURS.get(snap.get("exchange_tz") or "")
    if not hours:
        return None
    local = now.astimezone(ZoneInfo(snap["exchange_tz"]))
    h = local.hour + local.minute / 60
    open_h, close_h = hours
    if h <= open_h:
        return None
    return min(1.0, (h - open_h) / (close_h - open_h))


def volume_pace(snap: dict, now: datetime) -> float | None:
    """Today's volume vs what a normal day would have traded by now (1.0 = normal)."""
    vol, avg = snap.get("volume"), snap.get("avg_volume")
    if not vol or not avg:
        return None
    frac = session_fraction(snap, now)
    if frac is None:
        return None if (snap.get("market_state") or "").upper() == "REGULAR" else vol / avg
    return vol / (avg * max(frac, 0.1))


def split_suspect(d: float | None, pace: float | None, asset_class: str | None = None) -> bool:
    """A day move that matches a split ratio on ordinary volume is almost certainly a split
    (prices adjusted, quantity not yet) — not a crash or a rally. Only a stock splits, and only
    a KNOWN ordinary pace clears it: without volume a real −50% crash must not read as a split."""
    if d is None or pace is None or pace > SPLIT_MAX_PACE:
        return False
    if (asset_class or "equity").lower() != "equity":
        return False
    return any(abs(d - r) <= SPLIT_TOLERANCE_PCT for r in SPLIT_MOVES_PCT)
