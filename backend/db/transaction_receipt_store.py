"""TransactionReceiptStore — the transaction<->receipt many-to-many link table
(cross-channel-attachments RFC section 2.2; ADR
``2026-08-15-separate-attachments-receipts-and-statements.md`` "Transaction receipts" #5).

A transaction supports zero or more receipts; one receipt (e.g. a single itemized
invoice) may support more than one transaction — so this is a genuine link table, never a
foreign key on either side and never a list embedded on the transaction row itself.

Schema (transaction_receipts row):
    id             : str   ("trcpt_<12hex>")
    transaction_id : str
    receipt_id     : str
    linked_at      : str   (ISO 8601 UTC)
    audit_context   : dict  (who/what-surface linked it — ``core.audit_context.AuditScope``
                              snapshot at link time, or ``{}`` when none was active)

DB-backed (Tier-2 — table ``transaction_receipts``, backend/db/tables.py) via the generic
``PayloadTableStore`` (seq/id/payload) shape, mirroring ``receipts``'s own bare shape — see
that table's comment in ``db/tables.py`` for why no column is promoted. Checkpoint-EXCLUDED
and portable, matching ``receipts`` (its own delete/relink history should not be resurrected
or rewound independently of the receipt it links, which is itself excluded).
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import transaction_receipts


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TransactionReceiptStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(transaction_receipts, id_field="id", engine=engine)

    # ── Write ──────────────────────────────────────────────────────────────────

    def link(
        self,
        transaction_id: str,
        receipt_id: str,
        *,
        audit_context: dict | None = None,
        conn=None,
    ) -> dict:
        """Create ONE link row. Idempotency (never link the same pair twice) is the
        CALLER's job (``services.receipts.apply_receipt_change``) — this is a low-level
        mint primitive, matching every other store in this codebase's "the choke decides,
        the store just writes" split."""
        record = {
            "id": new_id("trcpt"),
            "transaction_id": transaction_id,
            "receipt_id": receipt_id,
            "linked_at": _now(),
            "audit_context": audit_context or {},
        }
        return self.insert(record, conn=conn)

    def unlink(self, link_id: str, *, conn=None) -> bool:
        return self.delete(link_id, conn=conn)

    # ── Read ───────────────────────────────────────────────────────────────────

    def by_transaction(self, transaction_id: str) -> list[dict]:
        """Every link row for one transaction, oldest first (``all()`` is seq-ordered)."""
        return [r for r in self.all() if r.get("transaction_id") == transaction_id]

    def by_receipt(self, receipt_id: str) -> list[dict]:
        """Every link row for one receipt, oldest first."""
        return [r for r in self.all() if r.get("receipt_id") == receipt_id]

    def find_link(self, transaction_id: str, receipt_id: str) -> dict | None:
        """The existing link row for this exact pair, or None — the idempotency check
        ``apply_receipt_change`` runs before inserting a new one."""
        for r in self.by_transaction(transaction_id):
            if r.get("receipt_id") == receipt_id:
                return r
        return None
