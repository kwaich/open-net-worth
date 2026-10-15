"""AgentFindingStore — the (now-vestigial) review-queue table for an agent run's output.

WAVE 5B (JSON->DB): the Agent product — and its own hand-rolled ``AgentFindingStore`` (a
``JsonStore`` subclass, ``id_field="id"``, prepend + capped at 200) — was retired in
``d0da1a09`` ("feat!: retire the Agent product"). Nothing in this codebase reads or
writes a finding anymore (verified: zero references to ``AgentFindingStore`` or
``AGENT_FINDINGS_JSON`` outside this store, ``core/paths.py``, and the checkpoint
exclusion list). This table exists ONLY to absorb ``agent_findings.json``'s real,
200-row historical queue byte-identically — this wave's "everything must be in
database, no runtime ``.json`` file" goal does not get to silently discard a
self-hoster's actual data just because the feature that produced it is gone.

Checkpoint-EXCLUDED (mirrors its own pre-existing ``AGENT_FINDINGS_JSON`` file-level
exclusion — a Lucius restore must not resurrect a stale finding, exactly like every
other behavioral/proposal log in this tier: ``corrections``, ``harness_suggestions``).

Kept as a thin ``PayloadTableStore`` subclass — not a bare table with no store at all —
purely for consistency with this codebase's repository-layer convention (every table in
``db/tables.py`` has a corresponding store in ``backend/db/``). This is deliberately NOT
a resurrection of the original store's agent-specific domain surface
(``query``/``append``/``mark_reviewed``/``dismiss``/``record_outcome``/
``recent_for_agent``/``find_recent_by_dedupe_key``) — every one of those verbs belongs to
a product that no longer exists, and re-adding them with zero callers would be dead code.
Generic ``PayloadTableStore`` reads (``all``/``find_by_id``/``query``/``count``) are all
this table needs to expose today.
"""

from __future__ import annotations

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import agent_findings


class AgentFindingStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(agent_findings, id_field="id", engine=engine)
