"""Write-only CoinGecko configuration (Demo or Pro tier) for market-data providers.

A facade over the two price-source connector rows (``db/price_source_store.py``): the tier and
the key live on ``coingecko`` (key in ``connector_secrets``), the Yahoo-first switch on
``yahoo_finance``. Settings come from the rows alone — a connector never reads the environment.
"""

from __future__ import annotations

from backend.db.connector_store import ConnectorStore, serialized_unit_of_work
from backend.db.engine import get_engine
from backend.db.price_source_store import (
    COINGECKO,
    DEFAULT_TIER,
    YAHOO_FINANCE,
    ensure_row,
    read,
)

_VALID_TIERS = frozenset({"demo", "pro"})


def _clean_key(value: object) -> str:
    raw = str(value or "")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError("CoinGecko API key contains invalid characters")
    return raw.strip()


def _clean_tier(value: object) -> str:
    """An unrecognized/absent tier falls back to Demo rather than raising — the tier is a
    closed UI choice (a select, never free text), so a stray value here can only come from a
    stale row or a hand-crafted request, and Demo is the safe (lower-quota, more restrictive
    host) side to fall back to."""
    tier = str(value or DEFAULT_TIER).strip().lower()
    return tier if tier in _VALID_TIERS else DEFAULT_TIER


class MarketDataConfigStore:
    """Public reads never expose the API key."""

    def runtime(self) -> dict:
        store = ConnectorStore()
        with get_engine().connect() as conn:
            coingecko = read(COINGECKO, conn=conn)
            yahoo = read(YAHOO_FINANCE, conn=conn)
            key = store.secrets(COINGECKO, conn=conn).get("api_key")
        return {
            "coingecko_api_key": _clean_key(key),
            "coingecko_api_key_tier": _clean_tier(coingecko["config"].get("tier")),
            "yahoo_primary_for_major_crypto": bool(
                yahoo["config"].get("primary_for_major_crypto", True)
            ),
        }

    def public(self) -> dict:
        runtime = self.runtime()
        return {
            "has_coingecko_key": bool(runtime["coingecko_api_key"]),
            "coingecko_api_key_tier": runtime["coingecko_api_key_tier"],
            "yahoo_primary_for_major_crypto": runtime["yahoo_primary_for_major_crypto"],
        }

    def update(self, changes: dict) -> dict:
        """Apply the public-settings patch atomically. ``coingecko_api_key`` follows the one
        secret rule every connector shares: a blank string KEEPS the saved key, ``None`` clears
        it (the only way to revoke a leaked key, since CoinGecko's Remove action refuses
        outright — ``services.connectors.remove_connector``), anything else replaces it. The
        key being ABSENT from ``changes`` leaves it untouched. ``None`` for the tier or the
        Yahoo switch means "no change"."""
        secrets: dict = {}
        if "coingecko_api_key" in changes:
            key = changes["coingecko_api_key"]
            secrets["api_key"] = None if key is None else _clean_key(key)
        tier = changes.get("coingecko_api_key_tier")
        primary = changes.get("yahoo_primary_for_major_crypto")

        store = ConnectorStore()
        with serialized_unit_of_work() as conn:
            ensure_row(store, conn, COINGECKO)
            ensure_row(store, conn, YAHOO_FINANCE)
            if secrets:
                store.set_secrets(COINGECKO, secrets, conn=conn)
            if tier is not None:
                store.patch_config(COINGECKO, {"tier": _clean_tier(tier)}, conn=conn)
            if primary is not None:
                store.patch_config(
                    YAHOO_FINANCE, {"primary_for_major_crypto": bool(primary)}, conn=conn
                )
        return self.public()
