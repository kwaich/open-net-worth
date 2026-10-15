"""Pure FX conversion math (RFC P1-A1 §3.6).

The conversion math (``to_base``, ``txn_base_amount``) and the base-currency read
(``base_currency``) previously lived in ``backend/ledger_service.py`` and were
imported *upward/sideways* by services, ai, monitoring, ingest, and other api
modules. They are pure arithmetic over a rates dict plus a trivial profile read,
so they belong in ``core/``. ``ledger_service`` now re-exports them (shims) so no
call site changes in this slice.

Breaking the ``core → cache-loader`` edge: the convenience no-``rates`` path uses
an injected loader (``set_fx_rates_loader``) instead of importing
``api.prices``/``services`` — same inversion pattern as the write-hook in
``core/io``. The default loader returns ``{base: 1.0}`` (matching the existing
``load_fx_cache`` fallback). Every hot path passes ``rates`` explicitly, so the
loader hook is only the zero-arg ergonomic fallback. This module imports nothing
internal at all — a pure leaf (``base_currency`` is injected the same way; see below).
"""

from __future__ import annotations

import logging
import math
from typing import Callable, Optional

from backend.core.errors import MissingFxRateError

_log = logging.getLogger(__name__)


# ── FX-rates loader hook ─────────────────────────────────────────────────────

# The no-``rates`` convenience path needs the cached FX rates, which are loaded by
# a side-effecting module higher in the stack (``services.fx_cache.load_fx_cache``,
# which registers itself as the loader at import). ``core/`` cannot import that, so
# the loader is injected. Until something registers a loader, fall back to the
# pivot-only table — identical to ``load_fx_cache``'s own fallback.
_fx_rates_loader: Optional[Callable[[], dict]] = None


def set_fx_rates_loader(fn: Optional[Callable[[], dict]]) -> None:
    """Install (or clear, with ``None``) the loader used by ``to_base(rates=None)``."""
    global _fx_rates_loader
    _fx_rates_loader = fn


def _load_rates() -> dict:
    if _fx_rates_loader is not None:
        try:
            return _fx_rates_loader()
        except Exception:  # pragma: no cover - defensive; fall back to pivot-only
            _log.debug("fx rates loader failed; using pivot-only fallback", exc_info=True)
    return {base_currency(): 1.0}


# ── Base currency (injected loader) + conversion math ─────────────────────────

# The base currency lives in the DB-backed profile store (``db/profile_store.py``), which is
# ABOVE ``core/`` — so, exactly like the FX-rates loader above, it is injected rather than read
# directly. ``services.fx_cache`` registers the real loader at import; until then (and on any
# failure) base_currency falls back to ``SGD``, the historical default.
_base_currency_loader: Optional[Callable[[], Optional[str]]] = None


def set_base_currency_loader(fn: Optional[Callable[[], Optional[str]]]) -> None:
    """Install (or clear, with ``None``) the loader used by ``base_currency()``."""
    global _base_currency_loader
    _base_currency_loader = fn


def base_currency() -> str:
    if _base_currency_loader is not None:
        try:
            return (_base_currency_loader() or "SGD").upper()
        except Exception:  # pragma: no cover - defensive; fall back to the default
            _log.debug("base-currency loader failed; using SGD default", exc_info=True)
    return "SGD"


def to_base(
    amount: float, currency: str, base: str | None = None, rates: dict | None = None
) -> float:
    """Convert `amount` in `currency` into the base currency using cached FX rates.

    fx_cache stores rates relative to the base currency (the pivot, anchored at 1.0):
    amount * rate[currency] / rate[base]. The math is pivot-invariant, so a preloaded
    `rates` dict on any pivot works; pass one to avoid a per-call cache read in hot loops
    (e.g. portfolio/intel scans).
    """
    if amount is None or amount == 0:
        return 0.0
    base = (base or base_currency()).upper()
    currency = (currency or base).upper()
    if currency == base:
        return round(float(amount), 2)
    if rates is None:
        rates = _load_rates()  # {currency: rate_vs_base}, includes base: 1.0
    rate = known_rate(currency, base, rates)
    if rate is None:
        raise MissingFxRateError(currency, base)
    return round(float(amount) * rate, 2)


def known_rate(currency: str, target: str, rates: dict | None = None) -> float | None:
    """How many target units one currency buys, or None for an unavailable pair."""
    currency = (currency or "").upper()
    target = (target or "").upper()
    if not currency or not target:
        return None
    if currency == target:
        return 1.0
    if rates is None:
        rates = _load_rates()
    cur_rate, target_rate = rates.get(currency), rates.get(target)
    try:
        if any(isinstance(value, bool) for value in (cur_rate, target_rate)):
            return None
        cur_rate, target_rate = float(cur_rate), float(target_rate)
        if not all(math.isfinite(value) and value > 0 for value in (cur_rate, target_rate)):
            return None
        rate = cur_rate / target_rate
        return rate if math.isfinite(rate) and rate > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def txn_base_amount(t: dict, base: str | None = None, rates: dict | None = None) -> float:
    """A transaction's reporting magnitude in the base currency.

    HISTORICAL-RATE LOCK (2026-07-09, ADR 2026-07-09-foreign-currency-historical-rate-lock):
    a completed transaction is a historical fact — its base value is fixed on the day it was
    written. When the row's stored `base_currency` still matches the current base, TRUST the
    write-time `base_amount` instead of revaluing it at today's rate (which silently drifted
    foreign-currency reporting). Only when the base currency has CHANGED since the write (stamp
    mismatch) or the row predates the stamp do we recompute from the native leg — the original
    behavior, which correctly re-expresses a stale snapshot in the new base.

    Expense → the money-out (from) leg; income → the money-in (to) leg; adjustment → whichever
    leg exists. A transfer IS stamped too: `resolve_transaction_row` → `_transfer_base_value`
    (`services/ledger.py`) writes the base value that moved, so this returns a real magnitude
    for transfers, not an absent one. That figure nets to zero for P&L, but nothing here filters
    it out — that's on the CALLER, e.g. `_txn_base_totals` (`services/ledger.py`), which keeps
    only income/expense rows before summing. Returns an absolute (unsigned) amount.
    """
    base = (base or base_currency()).upper()
    if (t.get("base_currency") or "").upper() == base and t.get("base_amount") is not None:
        return abs(float(t["base_amount"]))
    kind = t.get("kind", "expense")
    if kind == "income":
        amt, ccy = t.get("to_amount"), t.get("to_currency")
    elif kind == "expense":
        amt, ccy = t.get("from_amount"), t.get("from_currency")
    elif t.get("from_amount") is not None:
        amt, ccy = t.get("from_amount"), t.get("from_currency")
    else:
        amt, ccy = t.get("to_amount"), t.get("to_currency")
    if amt is None:  # no native leg → fall back to the snapshot
        return abs(float(t.get("base_amount") or 0))
    return abs(to_base(float(amt), ccy or base, base, rates=rates))


def allocated_leg_amount(t: dict) -> float | None:
    """The native magnitude of the leg a split allocates: the from leg for an expense, the to
    leg for income (mirrors ``txn_base_amount``'s primary-leg pick). None when that leg is
    absent. The denominator of the split-fraction — a split's lines sum to THIS amount."""
    kind = t.get("kind", "expense")
    amt = t.get("to_amount") if kind == "income" else t.get("from_amount")
    return abs(float(amt)) if amt is not None else None


def txn_split_base_amount(
    t: dict, split: dict, base: str | None = None, rates: dict | None = None
) -> float:
    """One split line's reporting magnitude in the CURRENT base (P3-SP).

    = ``split.amount / allocated_leg_amount × txn_base_amount(t)`` — the line's FRACTION of the
    leg, applied to the leg's current-base value. A pure ratio (NO stored per-split base), so it
    is base-change-safe by the same mechanism as ``txn_base_amount`` AND ``Σ over lines ==
    txn_base_amount`` exactly (the fractions sum to 1.0) — a split can never make the category
    total diverge from the leg total. Degenerate leg (0 / missing) → 0.0. Returns abs."""
    leg = allocated_leg_amount(t)
    if not leg:
        return 0.0
    amt = abs(float(split.get("amount") or 0.0))
    return round(amt / leg * txn_base_amount(t, base, rates), 2)
