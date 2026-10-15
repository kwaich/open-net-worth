"""FX-rate cache I/O (RFC P1-A5 §3.1).

The last-known ``{currency: rate_vs_base}`` map, read/written to ``fx_cache.json``.
Moved out of ``api.prices`` so services/ai/monitoring/ingest can read FX without an
up-import into ``api/``. Rates are stored relative to ``base_currency`` (the pivot,
anchored at 1.0); switching base invalidates the cache (cleared by the profile
base-change handler so the next fetch re-anchors on the new pivot).

This is side-effecting I/O (it touches the filesystem), so it lives in ``services/``
rather than ``core/`` — ``core/fx`` owns only the pure conversion math and reads
rates through the injected loader registered at the bottom of this module.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime

from backend.core.errors import MissingFxRateError, ValidationError
from backend.core.fx import base_currency, set_base_currency_loader, set_fx_rates_loader, to_base
from backend.core.io import atomic_write_text
from backend.core.paths import FX_CACHE_JSON as _FX_CACHE_FILE

# Serialises the read-merge-write cycle in save_fx_cache.  Concurrent price
# fetches (e.g. two scheduled price-check tasks firing at the same time) can
# otherwise race: both read the existing cache, both update it, and the second
# write silently drops the first's rates.  Torn files are prevented by
# atomic_write_text; the lock prevents lost updates.
_fx_cache_lock: threading.Lock = threading.Lock()
_readiness_lock = threading.Lock()
_readiness_slots = threading.BoundedSemaphore(4)
_readiness_fetches: dict[tuple[str, str], threading.Event] = {}
_READINESS_TIMEOUT = 15.0
_log = logging.getLogger(__name__)


def ensure_fx_rate(currency: str, amount: float) -> None:
    """Resolve an opening's FX before taking the account/ledger write lock.

    Concurrent retries share one fetch. A slow provider cannot hold the request or a
    database transaction indefinitely; a timed-out fetch may warm the cache for retry.
    """
    base = base_currency()
    currency = base if currency is None else currency
    if not isinstance(currency, str) or not (
        len(currency) == 3 and currency.isascii() and currency.isalpha()
    ):
        raise ValidationError("currency must be a three-letter currency code")
    currency = currency.upper()
    try:
        to_base(amount, currency, base=base)
        return
    except MissingFxRateError:
        pass

    key = (base, currency)
    with _readiness_lock:
        ready = _readiness_fetches.get(key)
        if ready is None:
            if not _readiness_slots.acquire(blocking=False):
                raise MissingFxRateError(currency, base)
            ready = threading.Event()
            _readiness_fetches[key] = ready
            try:
                threading.Thread(
                    target=_fetch_missing_rate,
                    args=(key, ready),
                    daemon=True,
                    name="opening-fx-fetch",
                ).start()
            except Exception:
                _readiness_fetches.pop(key, None)
                _readiness_slots.release()
                raise
    ready.wait(_READINESS_TIMEOUT)
    # Validate the persisted cache, not merely a successful provider response: the
    # ledger reads this same cache when it stamps the opening transaction.
    to_base(amount, currency)


def _fetch_missing_rate(key: tuple[str, str], ready: threading.Event) -> None:
    try:
        from backend.services.prices import fetch_live

        fetch_live([], [key[1]], record_history=False)
    except Exception:
        _log.warning("Opening FX fetch failed for %s/%s", *key, exc_info=True)
    finally:
        with _readiness_lock:
            _readiness_fetches.pop(key, None)
            _readiness_slots.release()
            ready.set()


def load_fx_cache() -> dict:
    """Return last-saved FX rates, anchored on the base currency (rates[base]=1.0).
    Falls back to a minimal {base: 1.0}."""
    base = base_currency()
    try:
        if _FX_CACHE_FILE.exists():
            with open(_FX_CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
                rates = data.get("rates", {})
                rates[base] = 1.0
                return rates
    except Exception:
        pass
    return {base: 1.0}


def save_fx_cache(fx: dict) -> None:
    """Persist FX rates (merged) so other modules (prompts, cashflow) can use them without a
    live fetch. Merges into the existing cache so a fetch that omits or fails to fetch a
    currency never wipes a previously-good rate — only real (truthy) rates overwrite.

    Rates are stored relative to the base currency (the pivot), which is anchored at 1.0.
    Switching base_currency invalidates this cache; update_profile clears it so the next
    fetch re-anchors on the new pivot."""
    try:
        with _fx_cache_lock:
            existing = load_fx_cache()  # {currency: rate_vs_base}, incl base: 1.0
            existing.update({k: v for k, v in fx.items() if v})
            atomic_write_text(
                _FX_CACHE_FILE,
                json.dumps({"rates": existing, "updated": datetime.now().isoformat()}),
            )
    except Exception:
        pass


# core.fx.to_base(rates=None) loads the FX cache via an injected loader so core/ stays
# leaf-pure (no import of api/services). Register the real loader here. The wrapper
# resolves load_fx_cache by name at call time (not a captured reference) so tests that
# monkeypatch this module's load_fx_cache still take effect.
set_fx_rates_loader(lambda: load_fx_cache())


_profile_store_for_base = None


def _load_base_currency() -> "str | None":
    """Base currency for ``core.fx.base_currency()`` — read from the DB-backed profile store
    (Wave 5, JSON->DB). Lazy store construction keeps this import-safe during early boot; the
    engine is always up by the time ``base_currency()`` is first called."""
    global _profile_store_for_base
    if _profile_store_for_base is None:
        from backend.db.profile_store import ProfileStore

        _profile_store_for_base = ProfileStore()
    return _profile_store_for_base.get_raw().get("base_currency")


set_base_currency_loader(_load_base_currency)
