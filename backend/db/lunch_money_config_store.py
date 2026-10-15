"""LunchMoneyConfigStore — config + fetch state for the Lunch Money aggregation connector.

A facade over the ``lunch_money`` row of ``ConnectorStore`` (see ``AggregatorConfigStore``): the
**access token** (a bearer credential over the user's own Lunch Money budget) lives in
``connector_secrets``; ``enabled`` in ``connectors.enabled``; the latest status and last-fetched
timestamp in ``connectors.state``. There is no scheduled poll and no provider-account map (RFC
``connectors-as-data-sources``): the provider-account → app-account link lives on
``Account.external_ids`` instead.

Secret discipline:

  * ``connector_secrets`` is SECRET (``core.sensitive.SECRET_TABLES``, never handed to a model),
    checkpoint-EXCLUDED and NON-RESTORABLE;
  * ``public()`` exposes only ``has_access_token`` — the token is NEVER returned to a client;
  * a blank ``access_token`` on update means "leave the saved one unchanged" (write-only).

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the Lunch
Money protocol + the sync orchestration live in ``services/aggregation/lunch_money.py``.
"""

from __future__ import annotations

from backend.db.aggregator_config_store import AggregatorConfigStore

DEFAULTS: dict = {
    "enabled": False,  # master switch
    "access_token": "",  # WRITE-ONLY — never returned to the frontend
    # status / fetch state
    "last_synced_at": None,  # "Last fetched" in the UI
    "last_status": "never_run",  # never_run | ok | auth_error | connect_error | error
    "last_error": None,
}


class LunchMoneyConfigStore(AggregatorConfigStore):
    KIND = "lunch_money"
    LABEL = "Lunch Money"
    DEFAULTS = DEFAULTS
    SECRET_KEYS = ("access_token",)
    STATE_KEYS = ("last_synced_at", "last_status", "last_error")

    # ── status helpers ───────────────────────────────────────────────────────────

    def access_token(self) -> str:
        return (self.get().get("access_token") or "").strip()

    def is_configured(self) -> bool:
        """Whether an access token is set (the one credential the connector needs)."""
        return bool(self.access_token())

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        """Config with the access token stripped → a ``has_access_token`` flag instead."""
        cfg = self.get()
        return {
            "configured": self.is_configured(),
            "enabled": cfg["enabled"],
            "has_access_token": bool((cfg.get("access_token") or "").strip()),
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
        }
