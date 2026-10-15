"""The two price-source connector rows (``coingecko``, ``yahoo_finance``) and their health state.

Price sources cannot be removed or paused, so they always exist: the connectors migration
creates both rows and this module creates a missing one on first write. Their settings
(CoinGecko's tier, Yahoo's "primary for major crypto" switch) live in ``config``, CoinGecko's
key in ``connector_secrets``, and everything the app writes about their health — the quota
window and the circuit breakers — in ``state`` (RFC ``connectors-table``). The telemetry event
log is large and written on every call, so it stays in its own ``runtime_state`` namespace.

Health is written on every upstream call, so each write is ONE small serialised transaction
(``ConnectorStore.update_state`` inside ``serialized_unit_of_work``): two workers reserving
quota can never both read the same window. Shared by ``MarketDataConfigStore`` and the three
health stores; none of them reads or writes the other's ``state`` keys.
"""

from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy import Connection

from backend.db.connector_store import ConnectorStore, serialized_unit_of_work

COINGECKO = "coingecko"
YAHOO_FINANCE = "yahoo_finance"

DEFAULT_TIER = "demo"

# connector id -> (display name, default config)
_PRICE_SOURCES: dict[str, tuple[str, dict]] = {
    COINGECKO: ("CoinGecko", {"tier": DEFAULT_TIER}),
    YAHOO_FINANCE: ("Yahoo Finance", {"primary_for_major_crypto": True}),
}


def ensure_row(store: ConnectorStore, conn: Connection, connector_id: str) -> None:
    """Create the price-source row if it is missing (enabled: they are always on)."""
    if store.get(connector_id, conn=conn) is None:
        name, config = _PRICE_SOURCES[connector_id]
        store.create(connector_id, connector_id, name, config=config, enabled=True, conn=conn)


def read(connector_id: str, *, conn: Optional[Connection] = None) -> dict:
    """The row, or what a fresh one would hold when it has not been created yet."""
    row = ConnectorStore().get(connector_id, conn=conn)
    if row is not None:
        return row
    name, config = _PRICE_SOURCES[connector_id]
    return {
        "id": connector_id,
        "kind": connector_id,
        "name": name,
        "enabled": True,
        "config": dict(config),
        "state": {},
    }


def update_state(connector_id: str, change: Callable[[dict], dict]) -> None:
    """Apply ``change(state) -> new state`` to the row's ``state`` in one transaction."""
    store = ConnectorStore()
    with serialized_unit_of_work() as conn:
        ensure_row(store, conn, connector_id)
        store.update_state(connector_id, change, conn=conn)
