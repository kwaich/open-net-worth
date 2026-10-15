"""Backfill ``emitter`` onto every stored email provenance ref.

``matching._same_source_event_veto`` stopped treating a NAMESPACE as an emitter (ADR
2026-09-02): two distinct ``email`` msgids are two distinct payments only when the same sender
issued both, because ``email`` carries every sender the book has ever received. The veto reads
``external_refs[].emitter`` to decide that, and fails CLOSED when it is absent — so without this
backfill every pre-existing row keeps the old, wrong behaviour forever.

The sender is already on the book: ``email_snapshots`` is keyed by the SAME short internal id the
ledger stores as ``ref`` (services/email_snapshots.py), and holds ``from_addr``. So this is a
pure join, no re-ingest and no network.

``transactions.payload`` is the round-trip source of truth and ``transaction_external_refs``'s
own ``payload`` holds each entry exactly as it appeared inside the parent's list
(backend/db/tables.py) — ``verify_transaction_children`` asserts the two agree, so BOTH are
rewritten here. ``source``/``ref`` are untouched; only the new key is added, and only where a
sender actually resolves.

Rows whose snapshot was never stashed or has since been pruned keep no emitter. That is a real
state, not a failure: unresolved reads as "may be the same emitter" and vetoes, exactly as
today. They resolve on their own for future writes, which stamp at the choke.

Revision ID: 202609021430_backfill_external_ref_emitter
Revises: 202608301845_merge_claims_and_asset_brief_news_drop
Create Date: 2026-09-02
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

revision = "202609021430_backfill_external_ref_emitter"
down_revision = "202608301845_merge_claims_and_asset_brief_news_drop"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def _senders(conn) -> dict[str, str]:
    """``{short internal id -> from_addr}`` for every stashed snapshot that has one."""
    out: dict[str, str] = {}
    for row_id, payload in conn.execute(sa.text("SELECT id, payload FROM email_snapshots")):
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, ValueError):
            continue
        addr = (data.get("from_addr") or "").strip()
        if row_id and addr:
            out[str(row_id)] = addr
    return out


def _stamp(entries: object, senders: dict[str, str]) -> tuple[list, bool]:
    """Add ``emitter`` to each email entry that resolves. Returns (entries, changed)."""
    if not isinstance(entries, list):
        return [], False
    changed = False
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("emitter"):
            continue
        if entry.get("source") != "email":
            continue
        addr = senders.get(str(entry.get("ref") or ""))
        if addr:
            entry["emitter"] = addr
            changed = True
    return entries, changed


def upgrade() -> None:
    conn = op.get_bind()
    senders = _senders(conn)
    if not senders:
        return

    stamped_txns = 0
    for txn_id, payload in conn.execute(sa.text("SELECT id, payload FROM transactions")).fetchall():
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        entries, changed = _stamp(data.get("external_refs"), senders)
        if not changed:
            continue
        data["external_refs"] = entries
        conn.execute(
            sa.text("UPDATE transactions SET payload = :p WHERE id = :i"),
            {"p": json.dumps(data), "i": txn_id},
        )
        stamped_txns += 1

    stamped_refs = 0
    for ref_id, source, ref, payload in conn.execute(
        sa.text("SELECT ref_id, source, ref, payload FROM transaction_external_refs")
    ).fetchall():
        if source != "email":
            continue
        addr = senders.get(str(ref or ""))
        if not addr:
            continue
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("emitter"):
            continue
        data["emitter"] = addr
        conn.execute(
            sa.text("UPDATE transaction_external_refs SET payload = :p WHERE ref_id = :i"),
            {"p": json.dumps(data), "i": ref_id},
        )
        stamped_refs += 1

    logger.info(
        "emitter backfill: %d transactions, %d ref rows stamped from %d snapshots",
        stamped_txns,
        stamped_refs,
        len(senders),
    )


def downgrade() -> None:
    """Strip the key back off both payloads. Lossless — every value is re-derivable from
    ``email_snapshots`` by re-running upgrade()."""
    conn = op.get_bind()

    for txn_id, payload in conn.execute(sa.text("SELECT id, payload FROM transactions")).fetchall():
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("external_refs"), list):
            continue
        changed = False
        for entry in data["external_refs"]:
            if isinstance(entry, dict) and entry.pop("emitter", None) is not None:
                changed = True
        if changed:
            conn.execute(
                sa.text("UPDATE transactions SET payload = :p WHERE id = :i"),
                {"p": json.dumps(data), "i": txn_id},
            )

    for ref_id, payload in conn.execute(
        sa.text("SELECT ref_id, payload FROM transaction_external_refs")
    ).fetchall():
        try:
            data = json.loads(payload) if isinstance(payload, str) else (payload or {})
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.pop("emitter", None) is not None:
            conn.execute(
                sa.text("UPDATE transaction_external_refs SET payload = :p WHERE ref_id = :i"),
                {"p": json.dumps(data), "i": ref_id},
            )
