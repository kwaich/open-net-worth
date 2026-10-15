"""SubscriptionStore — the (now-vestigial) subscription-detection overlay table.

WAVE 5C (JSON->DB): the subscription-detection feature this backed was retired well
before this wave (commit "feat(phase-2): payee registry, skill improvements, agent UX,
lots panel, CSS design system, subscription removal" — "Drop subscriptions module (moved
to payee/automation detection)"), taking its own hand-rolled ``SubscriptionStore`` (a
``JsonStore`` subclass, flat list, ``root_key=None``) with it. Nothing in this codebase
reads or writes a subscription overlay row anymore (verified: zero references to
``SubscriptionStore``/``subscription_store``/``/api/subscriptions`` anywhere under
``backend/`` or ``frontend/src`` outside this store and ``core/paths.py``). This table
exists ONLY to absorb ``subscriptions.json``'s real, 5-row historical overlay
(user dismiss/confirm decisions on a detected recurring-charge group) byte-identically —
mirrors ``agent_findings``' own post-retirement fate (Wave 5B; see
``docs/adr/2026-08-10-vestigial-subscriptions-absorbed-not-resurrected.md``) exactly.

Checkpoint-INCLUDED (unlike ``agent_findings``: ``SUBSCRIPTIONS_JSON`` never had a live
path constant, so it was never added to ``ai.checkpoint._EXCLUDED_PATHS`` — this table
preserves that never-excluded status, not a new classification. The rows are also
genuinely user-authored — a dismiss/confirm decision, not a behavioral log — so
checkpoint-INCLUDED is the right resting state regardless). NOT portable — mirrors
``SUBSCRIPTIONS_JSON``'s own pre-migration absence from ``core.portable.PORTABLE_FILES``.

Kept as a thin ``PayloadTableStore`` subclass — not a bare table with no store at all —
purely for consistency with this codebase's repository-layer convention (every table in
``db/tables.py`` has a corresponding store in ``backend/db/``). Generic
``PayloadTableStore`` reads (``all``/``find_by_id``/``query``/``count``) are all this
table needs to expose today — no ``dismiss``/``confirm`` domain surface, since the
feature that would call it no longer exists.
"""

from __future__ import annotations

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import subscriptions


class SubscriptionStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(subscriptions, id_field="id", engine=engine)
