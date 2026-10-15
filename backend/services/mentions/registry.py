"""Server-authoritative mention provider registry — single source of truth for
which types are mentionable, how to resolve them, and how to search them.

Phase 1: REGISTRY is now built from ``EntityDescriptor`` registrations in
``backend/services/mentions/descriptors/``, sorted by ``descriptor.order`` to
preserve the exact iteration order that the search engine's stable sort depends
on for same-rank tie-breaking (RFC §3.2 / §4.4).

``search.py`` and ``dispatch.py`` are unchanged — they continue consuming the
abstract ``MentionProvider`` dataclass.
"""

from __future__ import annotations

import backend.services.mentions.descriptors  # noqa: F401 — triggers register() calls
from backend.services.mentions._collect import build_descriptor_map, build_registry
from backend.services.mentions.descriptor import EntityDescriptor
from backend.services.mentions.types import MentionProvider, SearchQuery  # re-export

__all__ = ["MentionProvider", "SearchQuery", "REGISTRY", "KNOWN_TYPES", "DESCRIPTORS"]

REGISTRY: dict[str, MentionProvider] = build_registry()

# The descriptors behind REGISTRY, same order. Consumed by services.mentions.labels to
# turn a (type, id) pair into a labelled chip row without re-guessing per type.
DESCRIPTORS: dict[str, EntityDescriptor] = build_descriptor_map()

KNOWN_TYPES: frozenset[str] = frozenset(REGISTRY)
