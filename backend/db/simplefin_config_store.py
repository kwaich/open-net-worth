"""SimpleFinConfigStore — config + fetch state for the SimpleFIN bank-aggregation connector.

A facade over the ``simplefin`` row of ``ConnectorStore`` (see ``AggregatorConfigStore``): the
SimpleFIN **access URL** (an HTTP-basic-creds-embedded URL — a SECRET, anyone with it can read
the user's bank balances) lives in ``connector_secrets``; the master ``enabled`` flag in
``connectors.enabled``; the latest status and last-fetched timestamp in ``connectors.state``.
There is no scheduled poll and no provider-account map (RFC ``connectors-as-data-sources``):
the provider-account → app-account link lives on ``Account.external_ids`` instead.

Secret discipline:

  * ``connector_secrets`` is SECRET (``core.sensitive.SECRET_TABLES``, never handed to a model),
    checkpoint-EXCLUDED and NON-RESTORABLE;
  * ``public()`` exposes only ``has_access_url`` — the URL is NEVER returned to a client;
  * a blank ``access_url`` on update means "leave the saved one unchanged" (write-only).

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the SimpleFIN
protocol + the sync orchestration live in ``services/aggregation/simplefin.py``.
"""

from __future__ import annotations

from backend.db.aggregator_config_store import AggregatorConfigStore

DEFAULTS: dict = {
    "enabled": False,  # master switch
    "access_url": "",  # WRITE-ONLY — never returned to the frontend
    # status / fetch state
    "last_synced_at": None,  # "Last fetched" in the UI
    "last_status": "never_run",  # never_run | ok | auth_error | connect_error | error
    "last_error": None,
}


class SimpleFinConfigStore(AggregatorConfigStore):
    KIND = "simplefin"
    LABEL = "SimpleFIN"
    DEFAULTS = DEFAULTS
    SECRET_KEYS = ("access_url",)
    STATE_KEYS = ("last_synced_at", "last_status", "last_error")

    # ── status helpers ───────────────────────────────────────────────────────────

    def access_url(self) -> str:
        return (self.get().get("access_url") or "").strip()

    def is_configured(self) -> bool:
        """Whether an access URL is set (the one field the connector needs to fetch)."""
        return bool(self.access_url())

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        """Config with the access URL stripped → a ``has_access_url`` flag instead."""
        cfg = self.get()
        return {
            "configured": self.is_configured(),
            "enabled": cfg["enabled"],
            "has_access_url": bool((cfg.get("access_url") or "").strip()),
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
        }
