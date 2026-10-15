"""Descriptors for multi-surface types (class (c) per RFC §4.2).

skill (surfaces: verb + entity) uses search_override because the generic _row
hardcodes surface="entity" and the enabled-only-for-verb fan-out cannot be
expressed generically.  resolve_override covers the entity-chip dereference.
Both functions are imported verbatim from providers.py / resolve.py so behavior
is byte-identical.

``agent`` was the other member of this class until Agents were retired
(2026-08-02).

RFC 2026-06-25-lucius-generic-entity-registry §4.2 class (c).
"""

from __future__ import annotations

from backend.services.mentions._collect import register
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.providers import search_skill
from backend.services.mentions.resolve import _resolve_skill
from backend.services.mentions.types import SearchQuery

# ``rows`` below is read ONLY by the write-receipt chip's label path
# (``services.mentions.labels``) — search and resolve keep their multi-surface overrides,
# which the generic loop cannot express. The source is an unfiltered ``.all()`` so a chip
# for a just-deleted-then-restored skill still resolves its name.


def _skill_rows(query: SearchQuery) -> list[dict]:
    from backend.db.skill_store import SkillStore

    return SkillStore().all()


# ── skill (order=15) ──────────────────────────────────────────────────────────
# Multi-surface: "verb" = invokable via /, "entity" = talk-about context chip.
# search_skill emits enabled-only rows for "verb", all for "entity".

register(
    EntityDescriptor(
        type="skill",
        label="Skill",
        icon="⚡",
        order=15,
        tone="lucius",
        surfaces=frozenset({"verb", "entity"}),
        rows=_skill_rows,
        id_field="id",
        label_field="name",
        search_override=search_skill,
        resolve_override=_resolve_skill,
        fields={
            "name": FieldSpec("Name"),
            "description": FieldSpec("Description"),
            "enabled": FieldSpec("Enabled"),
        },
    )
)
