"""YnabConfigStore — config + fetch state for the YNAB aggregation connector.

A facade over the ``ynab`` row of ``ConnectorStore`` (see ``AggregatorConfigStore``). The
**access token** (a bearer credential over the user's own YNAB budget(s)) lives in
``connector_secrets``; the chosen **budget_id** is a setting, in ``connectors.config``; the
cached budget name/currency, the latest status and the last-fetched timestamp are
``connectors.state``; ``enabled`` is ``connectors.enabled``. There is no scheduled poll and no
provider-account map (RFC ``connectors-as-data-sources``): the provider-account → app-account
link lives on ``Account.external_ids`` instead.

Secret discipline:

  * ``connector_secrets`` is SECRET (``core.sensitive.SECRET_TABLES``, never handed to a model),
    checkpoint-EXCLUDED and NON-RESTORABLE;
  * ``public()`` exposes only ``has_access_token`` — the token is NEVER returned to a client;
  * a blank ``access_token`` on update means "leave the saved one unchanged" (write-only).

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the YNAB
protocol + the sync orchestration live in ``services/aggregation/ynab.py``.
"""

from __future__ import annotations

from backend.db.aggregator_config_store import AggregatorConfigStore

DEFAULTS: dict = {
    "enabled": False,  # master switch
    "access_token": "",  # WRITE-ONLY — never returned to the frontend
    "budget_id": None,  # explicit choice, or None = auto-detect when the token sees exactly one
    "budget_name": None,  # cached display name for the chosen budget (UI convenience only)
    "budget_currency": None,  # cached ISO currency code for the chosen budget (e.g. "USD")
    # status / fetch state
    "last_synced_at": None,  # "Last fetched" in the UI
    "last_status": "never_run",  # never_run | ok | auth_error | connect_error | error | ...
    "last_error": None,
}


class YnabConfigStore(AggregatorConfigStore):
    KIND = "ynab"
    LABEL = "YNAB"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("budget_id",)
    SECRET_KEYS = ("access_token",)
    STATE_KEYS = (
        "budget_name",
        "budget_currency",
        "last_synced_at",
        "last_status",
        "last_error",
    )

    def _normalize(self, clean: dict) -> None:
        """``budget_id`` is the ONE field that reads a BLANK STRING as an explicit instruction
        rather than a no-op: unlike the write-only token, the budget choice is not a secret, so
        there is no legitimate reason to "resend the current value to leave it unchanged" — a
        blank means the operator wants to CLEAR the pick (falling back to auto-detect-if-there-
        is-only-one on the next fetch), which also drops the cached name and currency."""
        if "budget_id" in clean:
            new_id = str(clean["budget_id"]).strip() or None
            clean["budget_id"] = new_id
            if new_id is None and "budget_name" not in clean:
                clean["budget_name"] = None
                clean["budget_currency"] = None

    # ── status helpers ───────────────────────────────────────────────────────────

    def access_token(self) -> str:
        return (self.get().get("access_token") or "").strip()

    def is_configured(self) -> bool:
        """Whether an access token is set (the one credential the connector needs to talk to
        YNAB at all). Deliberately does NOT require ``budget_id`` — budget resolution is a
        runtime step (auto-detect-if-one, or a clear "pick one" refusal), not a config gate,
        so the scheduler still attempts a sync (and can complete the auto-detection) as soon
        as a token is saved and enabled."""
        return bool(self.access_token())

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        """Config with the access token stripped -> a ``has_access_token`` flag instead."""
        cfg = self.get()
        return {
            "configured": self.is_configured(),
            "enabled": cfg["enabled"],
            "has_access_token": bool((cfg.get("access_token") or "").strip()),
            "budget_id": cfg.get("budget_id"),
            "budget_name": cfg.get("budget_name"),
            "budget_currency": cfg.get("budget_currency"),
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
        }
