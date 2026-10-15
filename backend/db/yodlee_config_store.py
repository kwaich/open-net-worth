"""YodleeConfigStore — config + fetch state for the Yodlee aggregation connector.

A facade over the ``yodlee`` row of ``ConnectorStore`` (see ``AggregatorConfigStore``). The
**client_id**, the **api_endpoint** (``sandbox`` | ``production``) and the linked user's
**username** (Yodlee loginName) are settings, in ``connectors.config``; the cobrand **secret**,
the user **password** and the two short-lived session tokens (machine-minted, never revealed,
refreshed on every sync) are in ``connector_secrets``; ``enabled`` is ``connectors.enabled``; the
latest status and last-fetched timestamp are ``connectors.state``.

Secret discipline:

  * ``connector_secrets`` is SECRET (``core.sensitive.SECRET_TABLES``, never handed to a model),
    checkpoint-EXCLUDED and NON-RESTORABLE;
  * ``public()`` exposes only ``has_secret`` / ``has_user_credentials`` flags — the secret,
    password and session tokens are NEVER returned to a client;
  * a blank write-only secret on update means "leave the saved one unchanged";
  * the value is NOT unreachable: the logged-in OPERATOR reads it back through
    ``services.connector_secrets`` (``GET /api/connectors/{id}/secret/{field}``), which is the
    ONLY module that returns one. A MODEL never can — ADR
    ``2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not``.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the Yodlee
protocol + the sync orchestration live in ``services/aggregation/yodlee.py``.
"""

from __future__ import annotations

from backend.db.aggregator_config_store import AggregatorConfigStore

# Yodlee per-environment API host. The sandbox is the Yodlee developer sandbox
# (free); production is the live data host.
YODLEE_HOSTS: dict[str, str] = {
    "sandbox": "https://sandbox.api.yodlee.com/ysl",
    "production": "https://production.api.yodlee.com/ysl",
}

DEFAULTS: dict = {
    "enabled": False,  # master switch
    "client_id": "",  # Yodlee clientId (treated as part of the credential pair)
    "secret": "",  # WRITE-ONLY — never returned to the frontend
    "api_endpoint": "sandbox",  # "sandbox" | "production"
    # Yodlee user credentials for the linked Yodlee user account (write-only).
    "username": "",  # Yodlee loginName for the linked user account
    "password": "",  # WRITE-ONLY — never returned to the frontend
    # Transient session tokens — refreshed on every sync; not secrets in the
    # traditional sense (short-lived), but treated as write-only so they are
    # never echoed to the frontend.
    "cobrand_session_token": "",  # WRITE-ONLY — active cobrand session
    "user_session_token": "",  # WRITE-ONLY — active user session
    # status / fetch state
    "last_synced_at": None,  # "Last fetched" in the UI
    "last_status": "never_run",  # never_run | ok | auth_error | connect_error | error
    "last_error": None,
}


class YodleeConfigStore(AggregatorConfigStore):
    KIND = "yodlee"
    LABEL = "Yodlee"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("client_id", "api_endpoint", "username")
    SECRET_KEYS = ("secret", "password", "cobrand_session_token", "user_session_token")
    STATE_KEYS = ("last_synced_at", "last_status", "last_error")

    def _normalized(self, merged: dict) -> dict:
        if merged.get("api_endpoint") not in YODLEE_HOSTS:
            merged["api_endpoint"] = DEFAULTS["api_endpoint"]
        return merged

    def _normalize(self, clean: dict) -> None:
        if "api_endpoint" in clean:
            ep = str(clean["api_endpoint"] or "").strip().lower()
            if ep in YODLEE_HOSTS:
                clean["api_endpoint"] = ep
            else:
                clean.pop("api_endpoint")

    # ── status helpers ───────────────────────────────────────────────────────────

    def client_id(self) -> str:
        return (self.get().get("client_id") or "").strip()

    def secret(self) -> str:
        return (self.get().get("secret") or "").strip()

    def api_endpoint(self) -> str:
        ep = (self.get().get("api_endpoint") or "").strip().lower()
        return ep if ep in YODLEE_HOSTS else DEFAULTS["api_endpoint"]

    def api_base(self) -> str:
        """The Yodlee REST API base URL for the configured endpoint."""
        return YODLEE_HOSTS[self.api_endpoint()]

    def username(self) -> str:
        return (self.get().get("username") or "").strip()

    def password(self) -> str:
        return (self.get().get("password") or "").strip()

    def cobrand_session_token(self) -> str:
        return (self.get().get("cobrand_session_token") or "").strip()

    def user_session_token(self) -> str:
        return (self.get().get("user_session_token") or "").strip()

    def has_secret(self) -> bool:
        """Whether the client_id + secret credential pair is set."""
        return bool(self.client_id()) and bool(self.secret())

    def has_user_credentials(self) -> bool:
        """Whether a Yodlee user account (loginName + password) is configured."""
        return bool(self.username()) and bool(self.password())

    def is_configured(self) -> bool:
        """Whether the connector is ready to fetch — requires both API credentials
        (client_id + secret) AND user credentials (username + password)."""
        return self.has_secret() and self.has_user_credentials()

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        """Config with the secrets stripped → ``has_secret`` / ``has_user_credentials``
        flags. The client_id is NOT a secret (the secret is), so it echoes back."""
        cfg = self.get()
        return {
            "configured": self.is_configured(),
            "enabled": cfg["enabled"],
            "api_endpoint": self.api_endpoint(),
            "has_secret": self.has_secret(),
            "client_id": cfg.get("client_id") or "",  # the id is not secret (the secret is)
            "has_user_credentials": self.has_user_credentials(),
            "username": cfg.get("username") or "",  # loginName is not secret (the password is)
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
        }
