"""Envelope schema — the budget-axis entity decoupled from category.

Mirrors ``models/account.py``: this describes the stored shape ``db/envelope_store.py``
writes; it does not gate the store's own coercion (the store factory remains the runtime
authority, same "scaffold only" posture as ``Account``).

Per the RFC, everything budget-axis (group membership, order, budget-axis archive) moves onto
the envelope; a category keeps only classification (kind/label/color/hidden).
Cardinality: one envelope maps N category keys; a category belongs to AT MOST one envelope
(``services.envelopes.env_of`` is single-valued) — enforced at the write choke point, not
here. Assigned amounts, targets, and notes are stored ENVELOPE-ID NATIVE in
``budget_assignments.json``/``profile.category_targets``/``profile.category_notes``
(docs/adr/2026-07-05-envelope-id-native.md) — never on the envelope row itself.

See ``.claude/rfcs/envelope-not-category.md`` §2-3.
"""

from __future__ import annotations

from typing import List, Optional

from .base import ONBaseModel


class Envelope(ONBaseModel):
    """The stored envelope shape. Assigned/Available/RTA/target/note are computed or stored
    elsewhere, never on this row — only identity and grouping metadata.

    ``color``/``archive_reason``/``merged_into``/``former_category_ids``/``archived_at``
    land with the budget-envelopes handoff port (Lane BE): a display colour, plus the archive
    metadata ``delete_envelope``/``merge_envelope`` stamped when they archived. They hard-delete
    since ADR 2026-09-26-deleting-an-envelope-deletes-it, so only rows archived before then carry
    it — read, never written again."""

    id: str
    name: str = ""
    category_ids: List[str] = []
    group_id: Optional[str] = None
    order: int = 0
    archived: bool = False
    color: str = ""
    archive_reason: str = ""  # "" | "deleted" | "merged"
    merged_into: str = ""  # envelope id — set only when archive_reason == "merged"
    former_category_ids: List[str] = []  # captured BEFORE the wipe, for restore
    archived_at: str = ""  # ISO timestamp
