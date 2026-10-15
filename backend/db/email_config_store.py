"""EmailConfigStore — the Email connector, as a facade over its ONE ``connectors`` row.

Email is one connector with one identity: ``address`` is the IMAP login, the SMTP login, the
From address and the recipient of email alerts, and one ``password`` serves both servers
(RFC ``connectors-table``). The row is ``connectors.id = 'email'``:

  * ``enabled``  — the single on/off (Pause/Resume), gating receive, send and the alert channel;
  * ``config``   — provider, address, imap_host/port/folder, lookback_days, smtp_host,
                   smtp_port, use_tls (nothing sensitive);
  * ``state``    — ``last_status`` / ``last_error`` written by the IMAP tools (``last_error``
                   is redacted by ``ConnectorStore.merge_state``);
  * secret ``password`` — in ``connector_secrets`` (SQL-denied, stripped from a sanitised
                   backup, non-restorable).

The row is created by the first ``update()``; until then every read answers from ``DEFAULTS``.
Nothing is seeded from the environment — the Email form is the only way in.

The password is NEVER returned by ``get()`` or ``public()`` (only a ``has_password`` flag). The
logged-in OPERATOR reads it back through ``services.connector_secrets``
(``GET /api/connectors/email/secret/password``), the ONLY module that returns one — a MODEL
never can. ADR ``2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not``.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.db.connector_store import ConnectorStore, serialized_unit_of_work

CONNECTOR_ID = "email"
_KIND = "email"
_NAME = "Email"

# The user-entered settings stored in ``connectors.config``.
DEFAULTS: dict = {
    "provider": None,
    "address": "",
    "imap_host": "",
    "imap_port": 993,
    "imap_folder": "INBOX",
    "lookback_days": 7,
    "smtp_host": "",
    "smtp_port": 587,
    "use_tls": True,
}

# last_status: never_run | ok | auth_error | connect_error | error
_STATE_DEFAULTS: dict = {"last_status": "never_run", "last_error": None}


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_changes(changes: dict) -> dict:
    """Coerce a settings patch: drop unknown keys and values that cannot be read as the
    right type, clamp the numeric ones. ``password`` and ``enabled`` ride along unchanged."""
    out: dict = {}
    for key, value in changes.items():
        if key == "enabled":
            out[key] = bool(value)
        elif key == "password":
            out[key] = value
        elif key not in DEFAULTS:
            continue
        elif key == "use_tls":
            out[key] = bool(value)
        elif key in ("imap_port", "smtp_port"):
            if (n := _int(value)) is not None:
                out[key] = n
        elif key == "lookback_days":
            if (n := _int(value)) is not None:
                out[key] = max(1, n)
        elif isinstance(value, str):
            out[key] = value.strip()
        else:
            out[key] = value
    return out


class EmailConfigStore:
    def __init__(self) -> None:
        self._store = ConnectorStore()

    # ── reads ──────────────────────────────────────────────────────────────────

    def get(self) -> dict:
        """The flat config object: defaults under the saved settings, plus ``enabled`` and the
        ``last_status`` / ``last_error`` state. No secret — see ``credentials()``."""
        row = self._store.get(CONNECTOR_ID)
        if row is None:
            return {**DEFAULTS, **_STATE_DEFAULTS, "enabled": False}
        return {
            **DEFAULTS,
            **row["config"],
            **_STATE_DEFAULTS,
            **row["state"],
            "enabled": row["enabled"],
        }

    def _password(self) -> str:
        return str(self._store.secrets(CONNECTOR_ID).get("password") or "").strip()

    # ── writes ─────────────────────────────────────────────────────────────────

    def update(self, changes: dict) -> dict:
        """Apply a settings patch in ONE transaction (row, config, enabled flag and password)
        and return ``get()``.

        Accepts the config keys, ``enabled`` and ``password``. A blank password KEEPS the saved
        one (the UI submits an empty field). Unknown keys are ignored.
        """
        clean = _clean_changes(changes)
        enabled = clean.pop("enabled", None)
        password = clean.pop("password", None)
        with serialized_unit_of_work() as conn:
            self._store.ensure(CONNECTOR_ID, _KIND, _NAME, conn=conn)
            if clean:
                self._store.patch_config(CONNECTOR_ID, clean, conn=conn)
            if enabled is not None:
                self._store.set_enabled(CONNECTOR_ID, enabled, conn=conn)
            if password is not None:
                self._store.set_secrets(CONNECTOR_ID, {"password": password}, conn=conn)
        return self.get()

    def record_status(self, status: str, error: Optional[str] = None) -> None:
        """Record the outcome of the latest IMAP call (``last_status`` / ``last_error``)."""
        if self._store.get(CONNECTOR_ID) is None:
            return
        self._store.merge_state(CONNECTOR_ID, {"last_status": status, "last_error": error})

    def reset(self) -> None:
        """Remove the connector's row and its password — the Remove-connector path
        (``services.connectors.remove_connector``). Replaces, never keeps: a removed
        connector's password is erased, not merely hidden."""
        self._store.delete(CONNECTOR_ID)

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        cfg = self.get()
        password = self._password()
        return {
            "enabled": cfg["enabled"],
            # Whether the account is SET UP — independent of ``enabled``. A Paused mailbox with
            # real saved credentials is not the same UI state as one never set up at all; the
            # OPERATIONAL gates (``credentials()`` / ``smtp_credentials()``) do check ``enabled``.
            "configured": bool(
                cfg["address"] and password and (cfg["imap_host"] or cfg["smtp_host"])
            ),
            "has_password": bool(password),
            "provider": cfg["provider"],
            "address": cfg["address"],
            "imap_host": cfg["imap_host"],
            "imap_port": cfg["imap_port"],
            "imap_folder": cfg["imap_folder"],
            "lookback_days": cfg["lookback_days"],
            "smtp_host": cfg["smtp_host"],
            "smtp_port": cfg["smtp_port"],
            "use_tls": cfg["use_tls"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
        }

    # ── credentials ──────────────────────────────────────────────────────────────

    def credentials(self, *, require_enabled: bool = True) -> dict | None:
        """The IMAP credentials, or None if incomplete or — unless ``require_enabled=False``
        (a connection test of a saved-but-paused account) — if the connector is paused."""
        cfg = self.get()
        password = self._password()
        if require_enabled and not cfg["enabled"]:
            return None
        if not (cfg["imap_host"] and cfg["address"] and password):
            return None
        return {
            "host": cfg["imap_host"],
            "port": int(cfg["imap_port"] or 993),
            "user": cfg["address"],
            "password": password,
            "folder": cfg["imap_folder"] or "INBOX",
            "lookback_days": int(cfg["lookback_days"] or 7),
        }

    def smtp_credentials(self, *, require_enabled: bool = True) -> dict | None:
        """The sender: SMTP server plus the one identity as login, From and alert recipient.
        None when incomplete, or — unless ``require_enabled=False`` — when the connector is
        paused. (``False`` lets "Send test" probe a saved-but-paused account.)"""
        cfg = self.get()
        password = self._password()
        if require_enabled and not cfg["enabled"]:
            return None
        if not (cfg["smtp_host"] and cfg["address"] and password):
            return None
        return {
            "host": cfg["smtp_host"],
            "port": int(cfg["smtp_port"] or 587),
            "user": cfg["address"],
            "password": password,
            "from_addr": cfg["address"],
            "to_addr": cfg["address"],
            "use_tls": bool(cfg["use_tls"]),
        }
