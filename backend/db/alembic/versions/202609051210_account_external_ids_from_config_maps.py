"""Move every provider ``account_map`` entry onto its account as ``external_ids`` (RFC
connectors-as-data-sources §D) — the last step retiring the nine provider config stores'
own ``{provider_account_id: app_account_id}`` blobs.

Nine ``secret_config`` rows (namespace ``"<provider>_config"``, ``db/payload_store.py``'s
``NamespacedSettingsStore`` shape) each carry an ``account_map``. For every entry, this
appends ``{source: <provider key>, ref: <provider account id>, name: "", mask: "",
linked_at: now}`` to the target account's ``external_ids`` (``accounts.payload``) — skipped
when the account no longer exists, or the pair is already present (idempotent: a second run
of this revision is a no-op). It then strips ``account_map``, ``poll_interval_seconds``,
``cursor``, ``server_knowledge`` off each provider's config payload — the scheduler that
read them is gone (RFC §E), and the link itself now lives on the account.

Writes RAW SQL directly against ``secret_config``/``accounts`` (mirrors
``202609021430_backfill_external_ref_emitter``'s shape) — no store import, so this revision
never depends on a store class's CURRENT shape outliving the schema it was written against.

NOT LOSSLESS: ``downgrade()`` reconstructs ``account_map`` from the ``external_ids`` this
revision itself added (tracked in-memory during ``upgrade()``, one run), but
``poll_interval_seconds``/``cursor``/``server_knowledge`` are gone for good — nothing reads
them once the scheduled sync pipeline is deleted (RFC §E), so there is nothing to restore
them FOR. A downgrade is therefore a partial, best-effort undo of the account-linking half
only, unlike the fully symmetric emitter backfill it otherwise mirrors.

Revision ID: 202609051210_account_external_ids_from_config_maps
Revises: 202609051200_connector_snapshots
Create Date: 2026-09-05
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202609051210_account_external_ids_from_config_maps"
down_revision = "202609051200_connector_snapshots"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

# provider key (the `source` value stamped on external_ids) -> its secret_config namespace.
_PROVIDER_NAMESPACES: dict[str, str] = {
    "simplefin": "simplefin_config",
    "plaid": "plaid_config",
    "lunch_money": "lunch_money_config",
    "lunch_flow": "lunch_flow_config",
    "ynab": "ynab_config",
    "enable_banking": "enable_banking_config",
    "yodlee": "yodlee_config",
    "alpaca": "alpaca_config",
    "ibkr": "ibkr_config",
}

_RETIRED_KEYS = ("account_map", "poll_interval_seconds", "cursor", "server_knowledge")


def _load(payload: object) -> dict:
    try:
        data = json.loads(payload) if isinstance(payload, str) else (payload or {})
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _account_payload(conn, account_id: str) -> dict | None:
    row = conn.execute(
        sa.text("SELECT payload FROM accounts WHERE id = :i"), {"i": account_id}
    ).first()
    return _load(row[0]) if row else None


def _link_account(conn, source: str, account_id: str, ref: str, now: str) -> bool:
    """Append one link to ``account_id``'s ``external_ids`` unless it's already there or the
    account doesn't exist. Returns whether a write happened."""
    data = _account_payload(conn, account_id)
    if data is None:
        return False
    ids = data.get("external_ids")
    ids = ids if isinstance(ids, list) else []
    if any(isinstance(e, dict) and e.get("source") == source and e.get("ref") == ref for e in ids):
        return False
    ids.append({"source": source, "ref": ref, "name": "", "mask": "", "linked_at": now})
    data["external_ids"] = ids
    conn.execute(
        sa.text("UPDATE accounts SET payload = :p WHERE id = :i"),
        {"p": json.dumps(data), "i": account_id},
    )
    return True


def _strip_retired_keys(config: dict) -> bool:
    changed = False
    for key in _RETIRED_KEYS:
        if key in config:
            del config[key]
            changed = True
    return changed


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()
    linked = 0
    for source, namespace in _PROVIDER_NAMESPACES.items():
        row = conn.execute(
            sa.text("SELECT payload FROM secret_config WHERE namespace = :n"), {"n": namespace}
        ).first()
        if row is None:
            continue
        config = _load(row[0])
        account_map = config.get("account_map")
        if isinstance(account_map, dict):
            for provider_account_id, app_account_id in account_map.items():
                if not isinstance(app_account_id, str) or not isinstance(provider_account_id, str):
                    continue
                if _link_account(conn, source, app_account_id, provider_account_id, now):
                    linked += 1
        if _strip_retired_keys(config):
            conn.execute(
                sa.text("UPDATE secret_config SET payload = :p WHERE namespace = :n"),
                {"p": json.dumps(config), "n": namespace},
            )
    logger.info("account_external_ids backfill: linked %d provider account(s)", linked)


def downgrade() -> None:
    """Best-effort, PARTIAL undo — see module docstring. Reconstructs each provider's
    ``account_map`` from the ``external_ids`` entries carrying its source, then removes
    those entries from the account. ``poll_interval_seconds``/``cursor``/``server_knowledge``
    are NOT restored — upgrade() did not keep their original values."""
    conn = op.get_bind()
    for source, namespace in _PROVIDER_NAMESPACES.items():
        account_map: dict[str, str] = {}
        for account_id, payload in conn.execute(
            sa.text("SELECT id, payload FROM accounts")
        ).fetchall():
            data = _load(payload)
            ids = data.get("external_ids")
            if not isinstance(ids, list):
                continue
            kept = [e for e in ids if not (isinstance(e, dict) and e.get("source") == source)]
            for entry in ids:
                if isinstance(entry, dict) and entry.get("source") == source and entry.get("ref"):
                    account_map[entry["ref"]] = account_id
            if len(kept) != len(ids):
                data["external_ids"] = kept
                conn.execute(
                    sa.text("UPDATE accounts SET payload = :p WHERE id = :i"),
                    {"p": json.dumps(data), "i": account_id},
                )
        if not account_map:
            continue
        row = conn.execute(
            sa.text("SELECT payload FROM secret_config WHERE namespace = :n"), {"n": namespace}
        ).first()
        if row is None:
            continue
        config = _load(row[0])
        config["account_map"] = account_map
        conn.execute(
            sa.text("UPDATE secret_config SET payload = :p WHERE namespace = :n"),
            {"p": json.dumps(config), "n": namespace},
        )
