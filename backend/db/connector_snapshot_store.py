"""ConnectorSnapshotStore — raw provenance for every row a bank/broker connector fetch
returned (RFC connectors-as-data-sources §C), the read-tools' sibling to
``EmailSnapshotStore``.

Row shape: ``{key, source, ref, provider_account_id, raw, normalized, content_hash,
first_seen_at, last_seen_at, changed_at, missed_fetches, last_missed_at, referenced}`` —
keyed by ``key = f"{source}:{ref}"`` (``id_field="key"``, mapped onto the table's generic
``id`` column, mirroring ``EmailSnapshotStore``'s own ``id_field="message_id"`` precedent).

Deliberately NOT first-seen-wins (the one load-bearing difference from
``EmailSnapshotStore.stash``): a bank/broker can correct or withdraw an already-fetched row,
and the whole point of this store is to notice. ``services.connector_snapshots`` owns that
diff logic; this class is data access only — insert/patch/query, no hashing, no change
detection.

Checkpoint-INCLUDED (durable user evidence, not a disposable cache) and portable
(``db/portable_bridge.py``), exactly like ``email_snapshots`` — see that store's own
docstring for the reasoning this one shares.
"""

from __future__ import annotations

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import connector_snapshots


def snapshot_key(source: str, ref: str) -> str:
    """The store's primary key for one fetched row — shared so the service layer never
    hand-rolls the ``f"{source}:{ref}"`` format differently than the store does."""
    return f"{source}:{ref}"


class ConnectorSnapshotStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(connector_snapshots, id_field="key", engine=engine)

    def get(self, source: str, ref: str) -> dict | None:
        return self.find_by_id(snapshot_key(source, ref))

    def by_source(self, source: str) -> list[dict]:
        """Every stashed row for one provider — the scope ``prune_unreferenced``/
        ``note_missing`` operate over (both are per-source, never global)."""
        return self.query(source=source)
