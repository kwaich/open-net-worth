"""AlpacaConfigStore — config + poll state for the Alpaca brokerage connector.

A facade over the ``alpaca`` row of ``ConnectorStore`` (see ``AggregatorConfigStore``). The
``environment`` (paper|live — the single source of truth for the base URL) is a setting, in
``connectors.config``; the **API key id** + **secret key** (a bearer credential pair) are in
``connector_secrets``; the master ``enabled`` flag (READ sync) is ``connectors.enabled``; the
latest status, last-synced timestamp and the display-only cash/buying-power read-out are
``connectors.state``. The provider-account → app-account link lives on
``Account.external_ids``.

This connector is READ-ONLY. The ``trading_enabled`` kill switch was REMOVED 2026-07-19 with
the rest of the trading half — see ``docs/adr/2026-07-19-remove-alpaca-trading-half.md``.

Secret discipline:

  * ``connector_secrets`` is SECRET (``core.sensitive.SECRET_TABLES``, never handed to a model),
    checkpoint-EXCLUDED and NON-RESTORABLE — a restore must never resurrect a superseded or
    revoked key pair;
  * ``public()`` exposes only ``has_keys`` (+ non-secret status/flags);
  * a blank ``api_key_id``/``secret_key`` on update means "leave the saved one unchanged";
  * the value is NOT unreachable: the logged-in OPERATOR reads it back through
    ``services.connector_secrets`` (``GET /api/connectors/{id}/secret/{field}``), which is the
    ONLY module that returns one. A MODEL never can — ADR
    ``2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not``.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the Alpaca
wire protocol + the position mapping live in ``services/aggregation/alpaca.py``.
"""

from __future__ import annotations

from typing import Optional

from backend.db.aggregator_config_store import AggregatorConfigStore

# The two base URLs are derived from ``environment`` — the rest of the code never hardcodes
# a URL (single source of truth, so a paper/live confusion is impossible at the call site).
PAPER_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_BASE_URL = "https://api.alpaca.markets"

DEFAULTS: dict = {
    "enabled": False,  # master switch for READ fetch (S1)
    "api_key_id": "",  # WRITE-ONLY — never returned to the frontend
    "secret_key": "",  # WRITE-ONLY — never returned to the frontend
    "environment": "paper",  # "paper" | "live"  (default: paper, to de-risk onboarding)
    # status / fetch state
    "last_synced_at": None,  # "Last fetched" in the UI
    "last_status": "never_run",  # never_run | ok | auth_error | rate_limited | connect_error | error
    "last_error": None,
    # display-only cash read-out from the last fetch (NOT net-worth math — see RFC §2.3)
    "cash": None,
    "buying_power": None,
}


class AlpacaConfigStore(AggregatorConfigStore):
    KIND = "alpaca"
    LABEL = "Alpaca"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("environment",)
    SECRET_KEYS = ("api_key_id", "secret_key")
    STATE_KEYS = ("last_synced_at", "last_status", "last_error", "cash", "buying_power")

    def _normalized(self, merged: dict) -> dict:
        if merged.get("environment") not in ("paper", "live"):
            merged["environment"] = "paper"
        return merged

    def _normalize(self, clean: dict) -> None:
        if "environment" in clean:
            env = str(clean["environment"]).strip().lower()
            if env in ("paper", "live"):
                clean["environment"] = env
            else:
                clean.pop("environment")

    def record_status(
        self,
        status: str,
        *,
        error: Optional[str] = None,
        cash: Optional[float] = None,
        buying_power: Optional[float] = None,
    ) -> None:
        """The one write a fetch call makes: status + "Last fetched" + error + the display-only
        cash/buying-power read-out (refreshed only by a run that succeeded and reported one — a
        ``None`` leaves the last reading in place rather than blanking it)."""
        state: dict = {
            "last_synced_at": self._now(),
            "last_status": status,
            "last_error": error,
        }
        if status == "ok":
            if cash is not None:
                state["cash"] = cash
            if buying_power is not None:
                state["buying_power"] = buying_power
        self._record(state)

    # ── helpers ──────────────────────────────────────────────────────────────────

    def keys(self) -> tuple[str, str]:
        """(api_key_id, secret_key) — the header pair for the Alpaca API."""
        cfg = self.get()
        return (cfg.get("api_key_id") or "").strip(), (cfg.get("secret_key") or "").strip()

    def environment(self) -> str:
        return self.get().get("environment") or "paper"

    def base_url(self) -> str:
        """The REST base URL derived from ``environment`` — single source of truth."""
        return LIVE_BASE_URL if self.environment() == "live" else PAPER_BASE_URL

    def is_configured(self) -> bool:
        """Whether BOTH keys are set (the credential the connector needs to fetch)."""
        key_id, secret = self.keys()
        return bool(key_id) and bool(secret)

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        """Config with the key pair stripped → a ``has_keys`` flag instead. Surfaces the
        non-secret status, the ``enabled`` read switch, the environment, and the
        cash/buying-power read-out."""
        cfg = self.get()
        return {
            "configured": self.is_configured(),
            "enabled": cfg["enabled"],
            "environment": cfg["environment"],
            "has_keys": self.is_configured(),
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
            "cash": cfg.get("cash"),
            "buying_power": cfg.get("buying_power"),
        }
