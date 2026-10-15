"""ChannelConfigStore — the Webhook connector, as a facade over its ONE ``connectors`` row.

The webhook channel (HTTP POST) registers on the ``core.channels`` seam. Email's alert channel
is part of the Email connector now (``EmailConfigStore``). The row is ``connectors.id =
'webhook'`` (RFC ``connectors-table``):

  * ``enabled``  — the on/off switch;
  * ``config``   — ``secret_header_name`` (nothing sensitive);
  * secrets ``url`` and ``secret`` — in ``connector_secrets`` (SQL-denied, stripped from a
    sanitised backup, non-restorable). The URL is a credential: a Slack/Discord incoming
    webhook URL is itself a bearer token.

Secret discipline:

  * ``public()`` exposes only ``has_url`` / ``has_secret`` and ``url_display`` (the host,
    which cannot authenticate) — neither value is ever returned to a client;
  * a blank secret on update means "leave the saved one unchanged";
  * the values are NOT unreachable: the logged-in OPERATOR reads them back through
    ``services.connector_secrets`` (``GET /api/connectors/webhook/secret/{url|secret}``), the
    ONLY module that returns one. A MODEL never can — ADR
    ``2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not``.

Nothing is seeded from the environment. The row is created by the first ``update()``.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the
transport (the actual HTTP send) lives in ``connectors/webhook/``.
"""

from __future__ import annotations

from backend.db.connector_store import ConnectorStore, serialized_unit_of_work
from backend.models.mcp_client import redact_endpoint

CONNECTOR_ID = "webhook"
_KIND = "webhook"
_NAME = "Webhook"

DEFAULTS: dict = {"secret_header_name": "X-Webhook-Secret"}

_SECRET_FIELDS = ("url", "secret")


class ChannelConfigStore:
    def __init__(self) -> None:
        self._store = ConnectorStore()

    # ── reads ──────────────────────────────────────────────────────────────────

    def get(self) -> dict:
        """The full config the sender needs: ``enabled``, ``secret_header_name`` and BOTH
        secrets (``url``, ``secret``). For the adapter in ``connectors/`` — never a view."""
        row = self._store.get(CONNECTOR_ID)
        cfg = {**DEFAULTS, **(row["config"] if row else {})}
        secrets = self._store.secrets(CONNECTOR_ID) if row else {}
        return {
            "enabled": bool(row and row["enabled"]),
            "secret_header_name": cfg["secret_header_name"] or DEFAULTS["secret_header_name"],
            "url": str(secrets.get("url") or "").strip(),
            "secret": str(secrets.get("secret") or "").strip(),
        }

    # ── writes ─────────────────────────────────────────────────────────────────

    def update(self, changes: dict) -> dict:
        """Patch the webhook in ONE transaction. A blank ``url`` / ``secret`` leaves the saved
        one unchanged; ``None`` values and unknown keys are ignored. Returns ``get()``."""
        clean = {k: v for k, v in changes.items() if v is not None}
        with serialized_unit_of_work() as conn:
            self._store.ensure(CONNECTOR_ID, _KIND, _NAME, conn=conn)
            if "secret_header_name" in clean:
                self._store.patch_config(
                    CONNECTOR_ID,
                    {"secret_header_name": str(clean["secret_header_name"]).strip()},
                    conn=conn,
                )
            if "enabled" in clean:
                self._store.set_enabled(CONNECTOR_ID, bool(clean["enabled"]), conn=conn)
            secrets = {k: str(clean[k]).strip() for k in _SECRET_FIELDS if k in clean}
            if secrets:
                self._store.set_secrets(CONNECTOR_ID, secrets, conn=conn)
        return self.get()

    def reset(self) -> None:
        """Remove the connector's row and its secrets — the Remove-connector path
        (``services.connectors.remove_connector``). Replaces, never keeps: a removed
        webhook's URL and secret are erased, not merely hidden."""
        self._store.delete(CONNECTOR_ID)

    # ── status helpers ───────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """Whether the webhook has the URL it needs to actually send."""
        return bool(self.get()["url"])

    def is_active(self) -> bool:
        """Enabled AND configured — the gate for registering on the core.channels seam."""
        cfg = self.get()
        return cfg["enabled"] and bool(cfg["url"])

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        """The webhook with its secrets stripped → ``has_*`` flags and the URL's host."""
        cfg = self.get()
        return {
            "name": CONNECTOR_ID,
            "enabled": cfg["enabled"],
            "secret_header_name": cfg["secret_header_name"],
            "has_url": bool(cfg["url"]),
            "url_display": redact_endpoint("http", cfg["url"]),
            "has_secret": bool(cfg["secret"]),
            "configured": bool(cfg["url"]),
        }

    def public_all(self) -> list[dict]:
        """The managed channels, secrets stripped — the API list shape."""
        return [self.public()]
