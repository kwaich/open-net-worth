"""LunchFlowConfigStore — config + OAuth tokens + poll state for the Lunch Flow bank/
investment-aggregation connector.

Lunch Flow (https://lunchflow.app) is a Plaid/Enable-Banking-shaped aggregation PLATFORM —
OAuth-redirect bank linking across 30+ countries — not a paste-a-token bridge like SimpleFIN
or Lunch Money. A facade over the ``lunch_flow`` row of ``ConnectorStore`` (see
``AggregatorConfigStore``), holding two credential tiers:

  * the OWNER's own **Developer Dashboard** credentials — `client_id` (a setting, in
    ``connectors.config``) + `client_secret` (a Basic-Auth pair, minted once by hand at
    lunchflow.app/developer, in ``connector_secrets``; mirrors how ``PlaidConfigStore`` holds
    Plaid's own `client_id`/`secret`);
  * the **per-connection OAuth tokens** — `access_token` / `refresh_token` (machine-minted
    secrets, in ``connector_secrets``, never revealed) and `token_expires_at` / `user_id`
    (``connectors.state``) — minted by the authorization-code exchange
    (``services.aggregation.lunch_flow_oauth.complete``) after the operator links a bank at
    Lunch Flow's own hosted login+consent page. This is a single-operator self-hosted app, so
    the platform's "one Lunch Flow user account" IS the operator — there is no per-end-user
    fan-out to model (see the connector's own ADR for that judgment call).

Secret discipline:

  * ``connector_secrets`` is SECRET (``core.sensitive.SECRET_TABLES``, never handed to a model),
    checkpoint-EXCLUDED and NON-RESTORABLE — the client secret and the OAuth tokens are all
    bearer credentials;
  * ``public()`` exposes only ``has_credentials`` / ``has_link`` (and non-secret flags) — the
    secret / tokens are NEVER returned to a client;
  * a blank ``client_secret`` on update means "leave the saved one unchanged" (write-only).
    ``access_token``/``refresh_token`` are never operator-typed at all — only the OAuth
    exchange (``lunch_flow_oauth.complete``/``ensure_fresh_token``) ever sets them;
  * the client secret (not the OAuth tokens — those are machine-minted, never operator-typed,
    same policy as Plaid's access_token) is reachable by the OPERATOR through
    ``services.connector_secrets`` (``GET /api/connectors/lunch_flow/secret/client_secret``) —
    ADR ``2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not``. A MODEL never
    can.

``email`` is the operator's own email address — pre-fills Lunch Flow's hosted login page
(``GET /oauth/authorize?...&email=...``) so they don't have to type it twice. It is NOT a
secret (a plain, editable, echoed-back field), matching how Enable Banking stores
`aspsp`/`country` as plain config rather than write-only.

``accounts_meta`` (``connectors.state``) is Lunch-Flow-specific institution bookkeeping,
independent of the provider-account → app-account LINK (which lives on
``Account.external_ids``, RFC ``connectors-as-data-sources``): Lunch Flow groups accounts under
a connection (one per linked institution), and ``DELETE /connections/{id}`` removes a whole
institution at once, so the UI needs to render "which accounts belong to which connection"
without a second network round trip.

The token and bookkeeping methods are each ONE serialized read+write transaction (or join the
caller's ``conn=``), so a refresh landing between a stale read and its write-back can never be
reverted.

Data-only repository (import-linter: ``db`` may import ``core``/``models`` only) — the Lunch
Flow OAuth protocol lives in ``services/aggregation/lunch_flow_oauth.py``, the sync
orchestration in ``services/aggregation/lunch_flow.py``.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Connection

from backend.db.aggregator_config_store import AggregatorConfigStore

DEFAULTS: dict = {
    "enabled": False,  # master switch
    # Owner's own Lunch Flow Developer Dashboard credentials.
    "client_id": "",
    "client_secret": "",  # WRITE-ONLY — never returned to the frontend
    # Pre-fills the hosted login page; not a secret.
    "email": "",
    # OAuth tokens minted by the authorization-code exchange — all write-only / machine-minted.
    "access_token": "",
    "refresh_token": "",
    "token_expires_at": None,  # epoch seconds (float) — Lunch Flow tokens expire after 1h
    "user_id": "",  # the Lunch Flow platform user id the tokens belong to (informational)
    # status / fetch state
    "last_synced_at": None,  # "Last fetched" in the UI
    "last_status": "never_run",  # never_run | ok | auth_error | connect_error | error
    "last_error": None,
    # Set by the polarity smell test (services.aggregation.lunch_flow._polarity_smell_test)
    # when a run's income/expense split looks like a backwards amount sign — independent of
    # last_status/last_error (a smell-test run still ends "ok": the fetch itself worked, the
    # DATA it produced looks suspicious). Always shown regardless of last_status, same as
    # last_error already is — see LunchFlowSection.tsx — so it can never go unseen on a
    # technically-successful first fetch. Cleared (None) on every run the smell test doesn't
    # fire on, exactly like last_error.
    "last_caution": None,
    # Lunch Flow account id (str) → {"connection_id": int, "institution_name": str, "name": str}
    # — institution bookkeeping only; the provider-account → app-account LINK lives on
    # ``Account.external_ids`` instead (RFC connectors-as-data-sources).
    "accounts_meta": {},
}

# Never accepted from an update() caller — only set_tokens()/apply_token_refresh() (the OAuth
# exchange/refresh path) may write these two.
_TOKEN_FIELDS = ("access_token", "refresh_token")


class LunchFlowConfigStore(AggregatorConfigStore):
    KIND = "lunch_flow"
    LABEL = "Lunch Flow"
    DEFAULTS = DEFAULTS
    CONFIG_KEYS = ("client_id", "email")
    SECRET_KEYS = ("client_secret", *_TOKEN_FIELDS)
    STATE_KEYS = (
        "token_expires_at",
        "user_id",
        "last_synced_at",
        "last_status",
        "last_error",
        "last_caution",
        "accounts_meta",
    )
    READ_ONLY_KEYS = _TOKEN_FIELDS

    def _normalized(self, merged: dict) -> dict:
        meta = merged.get("accounts_meta")
        merged["accounts_meta"] = meta if isinstance(meta, dict) else {}
        return merged

    def _normalize(self, clean: dict) -> None:
        for key in ("client_id", "email"):
            if key in clean:
                clean[key] = str(clean[key]).strip()

    # ── serialized writes ──────────────────────────────────────────────────────

    def reset(self, *, conn: Connection | None = None) -> None:
        """Remove the connector — the Remove path (``services.connectors.remove_connector``):
        the row, ``client_secret`` and the OAuth tokens are all erased.

        ``accounts_meta`` SURVIVES, deliberately — it is institution bookkeeping, not a
        credential. In practice the caller (``services.connectors.remove_connector``'s
        Lunch Flow branch) already best-effort-revokes every linked institution at Lunch
        Flow BEFORE calling this, which itself clears each revoked connection's entries via
        ``forget_connection`` — so what (if anything) survives here is only a connection a
        revoke call failed to reach, for which keeping the local metadata is still correct:
        the grant is still live at Lunch Flow, so the institution it names is still real."""
        with self._unit(conn) as c:
            meta = self.get(conn=c)["accounts_meta"]
            self._connectors.delete(self.KIND, conn=c)
            if meta:
                self._connectors.create(
                    self.KIND, self.KIND, self.LABEL, state={"accounts_meta": meta}, conn=c
                )

    def set_tokens(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_at: Optional[float],
        user_id: str,
        conn: Connection | None = None,
    ) -> dict:
        """Persist a freshly-exchanged token set (the OAuth ``complete()`` path only)."""
        return self._write_tokens(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_expires_at": expires_at,
                "user_id": user_id,
            },
            conn,
        )

    def record_run(
        self,
        *,
        synced_at: str,
        status: str,
        error: Optional[str],
        caution: Optional[str] = None,
        disable: bool = False,
        conn: Connection | None = None,
    ) -> dict:
        """Persist one sync run's outcome. Unlike ``update()``, ``None`` here is a REAL
        value, not "leave unchanged" — ``error``/``caution`` MUST be settable back to
        ``None`` (clearing a stale error/caution from an earlier run), which ``update()``
        cannot do: its None-means-unchanged contract exists for the REST config PATCH
        surface, where an omitted field really should be left alone. A sync run always knows
        its own authoritative status for this row, so it writes directly rather than routing
        through that filter.

        ``disable=True`` flips ``enabled`` off in the SAME write — used when a caution fires,
        so the scheduler can't keep silently repeating an unattended run once flagged."""
        with self._unit(conn) as c:
            self._ensure(c)
            self._apply(
                c,
                {
                    "last_synced_at": synced_at,
                    "last_status": status,
                    "last_error": error,
                    "last_caution": caution,
                    **({"enabled": False} if disable else {}),
                },
            )
            return self.get(conn=c)

    def record_status(self, status: str, *, error: Optional[str] = None) -> None:
        """The uniform ``record_status(status, error)`` surface every provider store
        exposes for ``connector_fetch.fetch()`` — a thin wrapper over ``record_run`` with an
        internally-computed timestamp and no caution/disable (a fresh fetch's own
        ``FetchWindow.warnings``, if any, are applied separately via a plain
        ``store.update({"last_caution": ...})`` — see that function's docstring)."""
        self.record_run(synced_at=self._now(), status=status, error=error)

    def apply_token_refresh(
        self, *, expected_access_token: str, conn: Connection | None = None, **fields: object
    ) -> dict:
        """Atomically apply a refreshed token set IFF the currently stored ``access_token``
        still equals ``expected_access_token`` — the compare-and-swap the refresh path needs
        so two concurrent refreshes land exactly one winning write (mirrors
        ``mcp/oauth_flow.py::ensure_fresh_token``'s re-check-before-write). A loser's call is
        a silent no-op — it returns the config the WINNER already wrote, which already has a
        valid token, so there is nothing left for the loser to do."""
        return self._write_tokens(fields, conn, expected_access_token=expected_access_token)

    def _write_tokens(
        self, fields: dict, conn: Connection | None, *, expected_access_token: str | None = None
    ) -> dict:
        """Token fields are written as given: unlike ``update()`` a blank secret CLEARS it
        (``None`` is how ``ConnectorStore.set_secrets`` clears one). With
        ``expected_access_token`` the write is a compare-and-swap on the stored access token."""
        with self._unit(conn) as c:
            self._ensure(c)
            current = self.get(conn=c)
            if expected_access_token is not None and (
                (current.get("access_token") or "") != expected_access_token
            ):
                return current  # someone else already refreshed it
            self._apply(
                c,
                {k: (v or None) if k in _TOKEN_FIELDS else v for k, v in fields.items()},
            )
            return self.get(conn=c)

    def record_account_meta(
        self,
        lf_account_id: str,
        *,
        connection_id: Optional[int] = None,
        institution_name: str = "",
        name: str = "",
        conn: Connection | None = None,
    ) -> None:
        """Persist one account's connection/institution metadata for the "linked accounts"
        UI and the disconnect flow (idempotent) — NOT the provider-account → app-account
        LINK, which lives on ``Account.external_ids`` instead."""
        with self._unit(conn) as c:
            self._ensure(c)
            meta = dict(self.get(conn=c)["accounts_meta"])
            meta[lf_account_id] = {
                "connection_id": connection_id,
                "institution_name": institution_name,
                "name": name,
            }
            self._apply(c, {"accounts_meta": meta})

    def forget_connection(self, connection_id: int, *, conn: Connection | None = None) -> list[str]:
        """Drop every account belonging to ``connection_id`` from the institution bookkeeping
        (called after a successful ``DELETE /connections/{id}``) — the Open Net Worth
        account(s) themselves, and any ``Account.external_ids`` link, are left untouched
        (same non-destructive posture as every other aggregation connector: unlinking never
        deletes ledger data). Returns the Lunch Flow account ids that were forgotten."""
        with self._unit(conn) as c:
            self._ensure(c)
            meta = dict(self.get(conn=c)["accounts_meta"])
            forgotten = [
                lf_id for lf_id, info in meta.items() if info.get("connection_id") == connection_id
            ]
            for lf_id in forgotten:
                meta.pop(lf_id, None)
            self._apply(c, {"accounts_meta": meta})
        return forgotten

    # ── status helpers ───────────────────────────────────────────────────────────

    def client_id(self) -> str:
        return (self.get().get("client_id") or "").strip()

    def client_secret(self) -> str:
        return (self.get().get("client_secret") or "").strip()

    def email(self) -> str:
        return (self.get().get("email") or "").strip()

    def access_token(self) -> str:
        return (self.get().get("access_token") or "").strip()

    def refresh_token(self) -> str:
        return (self.get().get("refresh_token") or "").strip()

    def token_expires_at(self) -> Optional[float]:
        value = self.get().get("token_expires_at")
        return float(value) if isinstance(value, (int, float)) else None

    def accounts_meta(self) -> dict:
        return dict(self.get().get("accounts_meta") or {})

    def connections(self) -> list[dict]:
        """Linked accounts grouped by Lunch Flow connection — one row per connection, each
        carrying its accounts. Derived from ``accounts_meta`` (never a second network call)."""
        groups: dict[int, dict] = {}
        for lf_id, info in self.accounts_meta().items():
            connection_id = info.get("connection_id")
            if connection_id is None:
                continue
            group = groups.setdefault(
                connection_id,
                {
                    "connection_id": connection_id,
                    "institution_name": info.get("institution_name") or "",
                    "accounts": [],
                },
            )
            group["accounts"].append(
                {
                    "lunch_flow_account_id": lf_id,
                    "name": info.get("name") or "",
                }
            )
        return sorted(groups.values(), key=lambda g: g["institution_name"] or "")

    def has_credentials(self) -> bool:
        """Whether the owner's client_id + client_secret developer credentials are set."""
        return bool(self.client_id()) and bool(self.client_secret())

    def has_link(self) -> bool:
        """Whether a bank has been linked (an OAuth token exchange has completed)."""
        return bool(self.access_token())

    def is_configured(self) -> bool:
        """Whether the connector is ready to FETCH — credentials AND a linked bank."""
        return self.has_credentials() and self.has_link()

    # ── public (non-secret) view for the API ─────────────────────────────────────

    def public(self) -> dict:
        """Config with the secrets stripped → ``has_*`` flags."""
        cfg = self.get()
        return {
            "configured": self.is_configured(),
            "enabled": cfg["enabled"],
            "has_credentials": self.has_credentials(),
            "client_id": cfg.get("client_id") or "",  # the id is not a secret (the secret is)
            "email": cfg.get("email") or "",
            "has_link": self.has_link(),
            "user_id": cfg.get("user_id") or "",
            "last_synced_at": cfg["last_synced_at"],
            "last_status": cfg["last_status"],
            "last_error": cfg["last_error"],
            "last_caution": cfg.get("last_caution"),
            "connections": self.connections(),
        }
