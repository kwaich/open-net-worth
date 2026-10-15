"""
Crash-watch — Phase 1 of the Analyst (RFC §11: "Crash-watch on all holdings").

A DETERMINISTIC, no-LLM lane that pings the user when ANY holding craters past a per-asset-class
threshold versus its prior close — even a holding with NO explicit price alert. Owning a holding is
enough: if it tanks, you hear about it, with zero setup. This module is PURE decision logic (no I/O,
no clock, no network); the price monitor owns the side effects (fetch, persist, push) and drives it
via ``evaluate()`` + ``build_signal()`` so the hard part — the cooldown/edge state machine — is unit
testable on its own.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.services.watchlist import CRASH_MOVE_PCT

# A fired crash is suppressed for this long UNLESS the holding recovers first (edge re-arm below).
# Bounds the worst case to one ping per holding per window even on a sustained, news-less slide.
COOLDOWN_HOURS = 4.0
# Recovery re-arm: once the drop shrinks back above HALF the threshold (e.g. an equity recovers from
# -12% to better than -6%), the cooldown clears so a FRESH leg-down re-fires. Edge-aware, not purely
# time-based — a dead-cat bounce then a second crash is two real events; a sticky -13% is one.
REARM_FRACTION = 0.5


def threshold_for(asset_class: str | None, overrides: dict | None = None) -> float | None:
    """Crash threshold (% down) for an asset class, or None when the class isn't crash-watched.

    ``overrides`` (keys equity/commodity/crypto, values float) shadows the module-level
    CRASH_MOVE_PCT constants — pass config.get('crash_thresholds') from price_monitor so
    user-configured thresholds are respected without importing watchlist config here.
    """
    key = (asset_class or "").lower()
    if overrides and key in overrides:
        return float(overrides[key])
    return CRASH_MOVE_PCT.get(key)


def move_pct(current: float | None, prev: float | None) -> float | None:
    """Signed % change vs prior close. None when either side is missing/non-positive (no move)."""
    if not prev or prev <= 0 or not current or current <= 0:
        return None
    return (current - prev) / prev * 100.0


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def evaluate(
    asset_class: str | None,
    current: float | None,
    prev: float | None,
    state: dict,
    now: datetime,
    overrides: dict | None = None,
) -> tuple[bool, float | None, dict]:
    """Pure crash decision for one holding.

    Returns ``(fire, move, new_state)``. ``state`` is the per-ticker crash record
    (``{"last_fired_at": iso|None}``); ``new_state`` is what the caller should persist. Down-moves
    ONLY — a rally is never a crash. Fires iff the move is at/below ``-threshold`` AND not within the
    cooldown window; a recovery above ``-threshold * REARM_FRACTION`` re-arms (clears the cooldown).
    ``overrides`` shadows CRASH_MOVE_PCT per-class (pass config crash_thresholds from price_monitor).
    """
    threshold = threshold_for(asset_class, overrides)
    new_state = dict(state or {})
    if threshold is None:
        return False, None, new_state
    mv = move_pct(current, prev)
    if mv is None:
        return False, None, new_state

    # Recovery re-arm: the drop has shrunk back above half the threshold → clear the cooldown so a
    # fresh leg-down can fire again. Done before the crash test so a same-run bounce can't self-block.
    if mv > -(threshold * REARM_FRACTION):
        new_state["last_fired_at"] = None

    if mv > -threshold:
        return False, mv, new_state  # down-move too small (or it's an up-move) — not a crash

    last = _parse(new_state.get("last_fired_at"))
    if last is not None and (now - last).total_seconds() < COOLDOWN_HOURS * 3600:
        return False, mv, new_state  # still cooling down — suppress the repeat ping

    new_state["last_fired_at"] = now.isoformat()
    return True, mv, new_state


def build_signal(
    *,
    ticker: str,
    label: str,
    currency: str,
    current: float,
    prev: float,
    mv: float,
    value_base: float,
    drop_base: float,
    base: str,
    now: str,
    instrument_id: str = "",
) -> dict:
    """Deterministic in-app signal for a fired crash — no LLM. Reuses ``type="price_alert"`` so the
    existing Intel signal surface renders it; the ``crash-watch-`` id namespace keeps it distinct
    from explicit price alerts (``price-alert-…``) so the two never collide on upsert.

    ``id`` embeds ``instrument_id`` (RFC intel-signals-data-model §2.4 — identity is
    instrument_id, never a raw ticker string). A crash-watched holding should always resolve
    one (``_collect_crash_items`` only admits rows from the hydrated portfolio); the
    ticker-keyed fallback is a defensive last resort only — this module stays pure (no
    logging call here), so the caller (``price_monitor._run_crash_lane``) logs it."""
    summary = (
        f"{label} ({ticker}) is down {abs(mv):.1f}% to {currency} {current:.4g} "
        f"(prior close {currency} {prev:.4g})."
    )
    impact = (
        f"Your position (~{base} {value_base:,.0f}) is down about {base} {abs(drop_base):,.0f} today."
        if value_base
        else ""
    )
    return {
        "id": f"crash-watch-{instrument_id or ticker.lower()}",
        "type": "price_alert",
        "instrument_id": instrument_id or None,
        "label": f"{label} — sharp drop",
        "ticker": ticker,
        "status": "action_needed",
        "current_summary": summary,
        "portfolio_impact": impact,
        "action_items": [f"Review {label} — check for news before acting"],
        "created_at": now,
        "updated_at": now,
    }
